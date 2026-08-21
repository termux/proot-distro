#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

# Architecture: The RUN handler — the only Dockerfile instruction that
# actually executes user code. The flow is:
#
#   1. Build the (shell-form or exec-form) command list.
#   2. Compute the recipe hash and consult the build cache. A hit
#      replays the cached layer; no proot exec.
#   3. Snapshot the rootfs, run the command under proot, snapshot
#      again, diff, and pack the delta into a gzipped layer that's
#      stored under its content digest in LAYER_CACHE_DIR.
#
# A step ends when nothing of it is still running, not when its root
# process exits. Docker gets that from the pid namespace it tears down;
# on Termux proot's --kill-on-exit extension kills its tracees; off
# Termux there was nothing, so `RUN service x start` -- or a plain
# `cmd &` -- left a process writing into the stage rootfs while the
# "after" snapshot was being taken, which makes a layer that differs
# from run to run, and left a daemon running long after the build.
# _stop_step() closes both halves of that: the process group proot
# leads, and the descendants that daemonise out of it, which land back
# on this process because it makes itself a child subreaper first.

import ctypes
import os
import signal
import subprocess
import tempfile
import time

from proot_distro.constants import (
    DEFAULT_PATH_ENV,
    DEFAULT_FAKE_KERNEL_RELEASE,
    DEFAULT_FAKE_KERNEL_VERSION,
    IS_TERMUX,
    TERMUX_PREFIX,
    PROGRAM_NAME,
)
from proot_distro import dirfd
from proot_distro.atomic import publish_file
from proot_distro.message import log_info, warn
from proot_distro.arch import (
    ARCH_UNAME_M, get_device_cpu_arch, get_emulator_args, get_proot_bin,
)
from proot_distro.shm import make_guest_tmp, make_shm_dir
from proot_distro.sysdata import setup_fake_sysdata, fake_sysdata_bindings
from proot_distro.helpers.build_cache import (
    compute_recipe_hash, lookup as cache_lookup, record as cache_record,
)
from proot_distro.helpers.build_engine.constants import PREDEFINED_ARGS
from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.build_engine.users import resolve_user_for_proot
from proot_distro.helpers.docker import (
    apply_layer, layer_cache_path, open_verified_layer,
)
from proot_distro.helpers.layer_diff import (
    diff_snapshots, snapshot, write_layer_tar,
)


def do_run(engine, instr):
    """RUN <cmd>: execute command under proot and snapshot the diff into a layer.

    Cache lookup happens first: a recipe-hash hit applies the cached
    layer and skips proot entirely. On a miss, snapshot the rootfs,
    exec under proot, snapshot again, pack the delta into a gzipped
    OCI layer, and record the (recipe-hash → layer) entry.
    """
    stage = engine.current

    if instr["exec_form"]:
        command = list(instr["value"])
        stdin_input = None
    else:
        heredocs = instr.get("heredocs") or []
        if heredocs:
            body = "\n".join(hd["body"] for hd in heredocs)
            command = list(stage.shell) + [body]
        else:
            command = list(stage.shell) + [str(instr["value"])]
        stdin_input = None

    # Cache lookup.
    extra = _run_extra_inputs(engine)
    recipe = compute_recipe_hash(
        stage.parent_layer_digest, instr, extra_inputs=extra
    )
    if not engine.no_cache:
        hit = cache_lookup(recipe)
        if hit is not None:
            # A recorded layer whose blob no longer hashes to its digest
            # is not a cache hit: the step is re-run instead, which is
            # what an evicted blob would have caused anyway.
            try:
                cached_fd = open_verified_layer(hit["layer_digest"])
            except RuntimeError:
                cached_fd = None
            if cached_fd is not None:
                try:
                    rootfs_fd = dirfd.opendir(stage.rootfs_dir)
                    try:
                        apply_layer(cached_fd, rootfs_fd,
                                    digest=hit["layer_digest"])
                    finally:
                        os.close(rootfs_fd)
                finally:
                    os.close(cached_fd)
                stage.layers.append({
                    "digest": hit["layer_digest"],
                    "size": hit["size"],
                    "diff_id": hit["diff_id"],
                })
                stage.parent_layer_digest = hit["layer_digest"]
                return

    engine.log("Indexing rootfs state...")
    before = snapshot(stage.rootfs_dir)
    exit_code = _exec_proot(engine, stage, command, stdin_input)
    if exit_code != 0:
        raise BuildError(
            f"RUN command failed at line {instr['lineno']} "
            f"with exit code {exit_code}."
        )

    engine.log("Capturing filesystem changes...")
    after = snapshot(stage.rootfs_dir)
    added, modified, deleted = diff_snapshots(before, after)
    paths_to_pack = added + modified

    if not (paths_to_pack or deleted):
        engine.log("No filesystem changes; emitting an empty layer.")
    else:
        engine.log(
            f"Packing layer: {len(added)} added, "
            f"{len(modified)} modified, {len(deleted)} deleted..."
        )

    tmp_layer_path = os.path.join(
        engine.tmp_root, f"layer-{stage.index}-{len(stage.layers)}.tar.gz"
    )
    digest, size, diff_id = write_layer_tar(
        stage.rootfs_dir, paths_to_pack, deleted, tmp_layer_path,
    )
    # Published through the same walk every other cache writer uses:
    # os.makedirs(dirname) plus os.replace(tmp, final) resolved the layer
    # cache by name, so a planted `oci_layers -> <host dir>` collected
    # what a build produced.
    publish_file(tmp_layer_path, layer_cache_path(digest))

    stage.layers.append(
        {"digest": digest, "size": size, "diff_id": diff_id}
    )
    stage.parent_layer_digest = digest
    cache_record(recipe, digest, diff_id, size, {})


def _run_extra_inputs(engine):
    """Encode env + ARG state visible to RUN for the recipe hash."""
    scope = engine.expansion_scope()
    items = sorted(scope.items())
    return "\n".join(f"{k}={v}" for k, v in items)


def _exec_proot(engine, stage, command, stdin_input):
    """Invoke proot against *stage*'s rootfs to execute *command*."""
    rootfs = stage.rootfs_dir
    # Absolute, from get_proot_bin(). subprocess resolves an executable
    # with no directory in it against os.get_exec_path(env) -- env being
    # child_env below, whose PATH is the image's and the stage's to set --
    # so a bare "proot" would let a Dockerfile's own `ENV PATH=...` pick
    # the binary this build execs outside any container.
    proot_bin = get_proot_bin()
    proot_args = [proot_bin]

    emu_args = get_emulator_args(
        stage.target_arch_pd, get_device_cpu_arch(), engine.emulator or "",
    )
    need_emu = bool(emu_args)
    proot_args += emu_args

    if IS_TERMUX:
        proot_args += ["--kill-on-exit", "--link2symlink", "--sysvipc"]
        uname_m = ARCH_UNAME_M.get(stage.target_arch_pd, os.uname().machine)
        proot_args.append(
            f"--kernel-release=\\Linux\\{PROGRAM_NAME}"
            f"\\{DEFAULT_FAKE_KERNEL_RELEASE}"
            f"\\{DEFAULT_FAKE_KERNEL_VERSION}\\{uname_m}\\localdomain\\-1\\"
        )
        proot_args.append("-L")

    uid, gid = resolve_user_for_proot(rootfs, stage.user)
    proot_args.append(f"--change-id={uid}:{gid}")
    proot_args.append(f"--rootfs={rootfs}")
    proot_args.append(f"--cwd={stage.workdir or '/'}")
    proot_args += ["--bind=/dev", "--bind=/proc", "--bind=/sys"]

    if IS_TERMUX:
        proot_args.append("--bind=/dev/urandom:/dev/random")
        if not os.path.lexists("/dev/fd"):
            proot_args.append("--bind=/proc/self/fd:/dev/fd")
        for i, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
            if not os.path.lexists(f"/dev/{name}") and os.path.exists(f"/proc/self/fd/{i}"):
                proot_args.append(f"--bind=/proc/self/fd/{i}:/dev/{name}")
        setup_fake_sysdata(rootfs)
        proot_args += fake_sysdata_bindings(rootfs)
        # /dev/shm comes from the stage's own directory, next to the
        # rootfs rather than inside it: a bind source is a name proot
        # resolves when it mounts it, and a process an earlier RUN step
        # left running can re-point one inside the rootfs in between —
        # nothing kills such a process off Termux, --kill-on-exit being a
        # Termux-only extension. It also keeps what a step writes to
        # /dev/shm out of the layer the step produces, which is what
        # Docker does with its tmpfs. `login` gives a container the same
        # two directories the same way; see proot_distro.shm.
        make_guest_tmp(rootfs)
        shm = make_shm_dir(rootfs)
        if shm is not None:
            proot_args.append(f"--bind={shm}:/dev/shm")
        else:
            warn("the stage's shm directory is not a plain directory; "
                 "running this step without the /dev/shm bind.")

    if need_emu and IS_TERMUX:
        for path in (
            "/apex", "/odm", "/product", "/system",
            "/system_ext", "/vendor",
            "/linkerconfig/ld.config.txt",
            "/plat_property_contexts", "/property_contexts",
        ):
            if os.path.exists(path):
                proot_args.append(f"--bind={path}")
        proot_args.append(f"--bind={TERMUX_PREFIX}")

    proot_args.extend(command)

    child_env = _build_child_env(stage)

    if not engine.quiet and not engine.verbose:
        log_info(f"Running step (user={stage.user or 'root'}, "
                 f"cwd={stage.workdir or '/'})...")

    # Before anything is spawned, so a descendant that outlives its own
    # parent is reparented here rather than onto init.
    _become_subreaper()
    baseline = set(_adopted())

    try:
        stdin_file = _stdin_file(engine, stdin_input)
    except OSError as exc:
        raise BuildError(f"cannot stage the step's input: {exc}") from exc
    try:
        proc = subprocess.Popen(
            proot_args,
            env=child_env,
            stdin=stdin_file if stdin_file is not None else subprocess.DEVNULL,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        raise BuildError(f"proot binary not available: {exc}") from exc
    finally:
        if stdin_file is not None:
            stdin_file.close()

    try:
        _wait_for_step(proc, baseline)
    except KeyboardInterrupt:
        # proot goes with the rest here: the build is over either way.
        _stop_step(proc.pid, baseline, quiet=True)
        proc.wait()
        raise

    _stop_step(proc.pid, baseline, skip_pid=proc.pid)
    try:
        return proc.wait(timeout=_STRAY_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        # Nothing of the step should still be holding proot open, but a
        # step that cannot be settled must not hang the build either.
        proc.kill()
        return proc.wait()


def _stdin_file(engine, stdin_input):
    """The here-doc body as a file to read stdin from, or None.

    A file rather than a pipe. The step is watched rather than waited on
    (see _wait_for_step), so there is no communicate() left to feed a
    pipe while it runs, and a body larger than the pipe buffer would
    deadlock against a step that is not reading yet. TemporaryFile
    unlinks it as it creates it, inside the build's own scratch root.
    """
    if stdin_input is None:
        return None
    try:
        fh = tempfile.TemporaryFile(dir=engine.tmp_root)
    except OSError:
        fh = tempfile.TemporaryFile()
    try:
        fh.write(stdin_input.encode())
        fh.seek(0)
    except BaseException:
        fh.close()
        raise
    return fh


# ---------------------------------------------------------------------------
# When a step is over, and what it leaves behind
# ---------------------------------------------------------------------------

# How often the step is looked at, and how long its leftovers get to
# take a SIGTERM before the SIGKILL.
_STEP_POLL_INTERVAL = 0.05
_STRAY_GRACE_SECONDS = 2.0

# prctl(2), Linux 3.4 and up, Android included.
_PR_SET_CHILD_SUBREAPER = 36

_subreaper_asked = None


def _become_subreaper() -> bool:
    """Ask the kernel to reparent orphaned descendants onto this process.

    This is what makes a step's leftovers findable at all. A backgrounded
    command outlives the shell that started it and a daemon goes further
    -- fork, setsid, fork, which leaves the step's process group as well
    -- and either way the process is then no relation of ours that any
    interface will name. As a subreaper it is reparented here instead of
    onto init, so /proc/self/task/<pid>/children lists it.

    Asked once, best effort: a kernel (or a seccomp filter) that refuses
    leaves _wait_for_step nothing to notice and the step is waited on the
    way it always was.
    """
    global _subreaper_asked
    if _subreaper_asked is None:
        try:
            libc = ctypes.CDLL(None, use_errno=True)
            _subreaper_asked = libc.prctl(
                _PR_SET_CHILD_SUBREAPER, 1, 0, 0, 0
            ) == 0
        except (OSError, AttributeError, ValueError):
            _subreaper_asked = False
    return _subreaper_asked


def _children_of(pid: int) -> list:
    """The PIDs the kernel currently calls children of *pid*."""
    try:
        with open(f"/proc/{pid}/task/{pid}/children") as fh:
            data = fh.read()
    except OSError:
        return []
    return [int(tok) for tok in data.split() if tok.isdigit()]


def _adopted(baseline=(), skip_pid=None) -> list:
    """The step's descendants the kernel has reparented onto this process.

    *baseline* is what was already there when the step started, which
    should be nothing -- the previous step is stopped before the next one
    begins -- but a straggler that would not die must not be counted
    against every step that follows it.
    """
    mine = os.getpid()
    return [
        pid for pid in _children_of(mine)
        if pid != skip_pid and pid not in baseline
    ]


def _group_members(pgid: int, skip=()) -> list:
    """The PIDs in process group *pgid*, minus *skip*.

    Read out of /proc rather than signalled as a group, because proot
    leads that group and must be left alone until it has reported the
    step's exit status. The parse takes the fields after the last ')',
    since a comm can hold anything at all, including one.
    """
    try:
        names = os.listdir("/proc")
    except OSError:
        return []
    members = []
    for name in names:
        if not name.isdigit():
            continue
        pid = int(name)
        if pid in skip:
            continue
        try:
            with open(f"/proc/{pid}/stat") as fh:
                data = fh.read()
        except OSError:
            continue
        try:
            fields = data[data.rindex(")") + 1:].split()
            if int(fields[2]) == pgid:
                members.append(pid)
        except (ValueError, IndexError):
            continue
    return members


def _wait_for_step(proc, baseline) -> None:
    """Wait for the step's command, not for the last thing it started.

    proot's event loop ends when its last *tracee* does, so a step that
    leaves anything running kept proot alive and the build waited on it
    for as long as that process ran -- `RUN service x start` never
    returned at all. Termux's proot has --kill-on-exit for exactly this;
    upstream proot has no equivalent, so the step is watched instead.

    proot's only child is the root tracee, so an empty children list
    means the step's command has finished. Everything still running is
    then something it left behind, and everything it left behind has by
    then been orphaned onto this process (see _become_subreaper), which
    is the cheap thing to look at -- one small read per poll rather than
    a scan of /proc. With no adopted process there is nothing to stop
    and proot is about to exit on its own, so the wait simply continues.
    """
    while True:
        try:
            proc.wait(timeout=_STEP_POLL_INTERVAL)
            return
        except subprocess.TimeoutExpired:
            pass
        if _children_of(proc.pid):
            continue
        if _adopted(baseline, skip_pid=proc.pid):
            return


def _signal_leftovers(targets, sig) -> None:
    """Deliver *sig* to each leftover, and to any group it leads.

    A daemonised leftover called setsid, so it leads a group of its own
    and that group goes too -- otherwise its children outlive it by a
    generation. killpg(pid) can only ever reach a group led by that very
    process, a group id being the pid of its leader, so this cannot
    reach anything the step did not start.
    """
    for pid in targets:
        try:
            os.kill(pid, sig)
        except OSError:
            pass
        try:
            os.killpg(pid, sig)
        except OSError:
            pass


def _reap(pids) -> None:
    """Collect the leftovers that have exited, so none linger as zombies.

    Named one at a time rather than with waitpid(-1), which could take
    proot's own status out from under Popen.wait() -- that call then
    reports ECHILD as a clean exit, and a step that failed would pass.
    A leftover reaped a moment too early to be a zombie is collected on
    the next round of the sweep, or left to the next step, which knows
    what was already there and does not count it again.
    """
    for pid in pids:
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass


def _leftovers(pgid: int, baseline, skip_pid) -> list:
    """Every process the step still has running, most-derived first.

    This process and the group it is in are never among them. A step
    runs in a session of its own, so its group id can only be proot's;
    one that is this program's own group would mean the caller got the
    pgid wrong, and the answer to that must not be a SIGTERM to
    everything sharing a terminal with the build.
    """
    found = list(_adopted(baseline, skip_pid=skip_pid))
    skip = set(found)
    skip.add(os.getpid())
    if skip_pid is not None:
        skip.add(skip_pid)
    if pgid is not None and pgid != os.getpgrp():
        for pid in _group_members(pgid, skip=skip):
            found.append(pid)
    return found


def _stop_step(pgid: int, baseline=(), *, skip_pid=None,
               quiet: bool = False) -> int:
    """Stop whatever the step still has running. Returns how many it found.

    *skip_pid* is proot, while it is still owed the chance to report the
    step's exit status; on the interrupt path it goes with the rest.

    Costs one small /proc read when the step ended cleanly, which is the
    usual case and the only one on Termux, where proot's --kill-on-exit
    has already done this.
    """
    targets = _leftovers(pgid, baseline, skip_pid)
    if not targets:
        return 0

    found = len(targets)
    _signal_leftovers(targets, signal.SIGTERM)
    deadline = time.monotonic() + _STRAY_GRACE_SECONDS
    while time.monotonic() < deadline:
        _reap(targets)
        targets = _leftovers(pgid, baseline, skip_pid)
        if not targets:
            break
        time.sleep(_STEP_POLL_INTERVAL)

    targets = _leftovers(pgid, baseline, skip_pid)
    if targets:
        _signal_leftovers(targets, signal.SIGKILL)
        _reap(targets)

    if not quiet:
        warn(f"the step left {found} process(es) running after its command "
             f"finished; they were stopped, so the layer captures a "
             f"settled rootfs.")
    return found


def _build_child_env(stage):
    env = {}
    env["PATH"] = stage.env.get("PATH") or DEFAULT_PATH_ENV
    env["HOME"] = stage.env.get("HOME", "/root")
    env["TERM"] = os.environ.get("TERM", "") or "xterm-256color"
    host_colorterm = os.environ.get("COLORTERM", "")
    if host_colorterm:
        env["COLORTERM"] = host_colorterm

    # Predefined ARGs from the host environment (proxies etc.) are
    # passed through even if the Dockerfile didn't declare them.
    for k in PREDEFINED_ARGS:
        v = os.environ.get(k, "")
        if v:
            env[k] = v

    # Declared ARGs in this stage.
    for k in stage.declared_args:
        if k in stage.args:
            env[k] = stage.args[k]

    # ENVs always win.
    for k, v in stage.env.items():
        env[k] = v

    # proot toggles inherited from host.
    for var in ("PROOT_NO_SECCOMP", "PROOT_VERBOSE"):
        v = os.environ.get(var, "")
        if v:
            env[var] = v
    if IS_TERMUX:
        # Where proot puts the backing files --link2symlink stands in for.
        # Through a descriptor for the same reason /tmp is: an image (or an
        # earlier step) can leave `.l2s` behind as a symlink, and proot
        # would then write every hard-link backing file into whatever host
        # directory it named. Unset rather than pointed somewhere
        # unvalidated -- proot then places each intermediate next to its
        # original, which is what it did before the pin existed.
        l2s_dir = dirfd.makedirs_under(stage.rootfs_dir, (".l2s",))
        if l2s_dir is not None:
            env["PROOT_L2S_DIR"] = l2s_dir
        else:
            # As in login: "unset" has to actually unset, or a value
            # from somewhere else stands in the fallback's place.
            env.pop("PROOT_L2S_DIR", None)
            warn("rootfs .l2s is not a plain directory; leaving "
                 "PROOT_L2S_DIR unset for this step.")
    env.pop("LD_PRELOAD", None)
    return env
