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

import os
import signal
import subprocess
import shutil

from proot_distro.constants import (
    DEFAULT_PATH_ENV,
    DEFAULT_FAKE_KERNEL_RELEASE,
    DEFAULT_FAKE_KERNEL_VERSION,
    IS_TERMUX,
    TERMUX_PREFIX,
    PROGRAM_NAME,
)
from proot_distro.message import log_info
from proot_distro.arch import (
    ARCH_UNAME_M, get_device_cpu_arch, get_emulator_args,
)
from proot_distro.sysdata import setup_fake_sysdata, fake_proc_bindings
from proot_distro.helpers.build_cache import (
    compute_recipe_hash, lookup as cache_lookup, record as cache_record,
)
from proot_distro.helpers.build_engine.constants import PREDEFINED_ARGS
from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.build_engine.users import resolve_user_for_proot
from proot_distro.helpers.docker import apply_layer, layer_cache_path
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
            cached_path = layer_cache_path(hit["layer_digest"])
            if os.path.isfile(cached_path):
                apply_layer(cached_path, stage.rootfs_dir)
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
    final_path = layer_cache_path(digest)
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    os.replace(tmp_layer_path, final_path)

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
    proot_bin = shutil.which("proot") or "proot"
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
        proot_args += [
            "--bind=/dev/urandom:/dev/random",
            "--bind=/proc/self/fd:/dev/fd",
        ]
        for i, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
            if os.path.exists(f"/proc/self/fd/{i}"):
                proot_args.append(f"--bind=/proc/self/fd/{i}:/dev/{name}")
        setup_fake_sysdata(rootfs)
        sysdata_dir = os.path.join(os.path.dirname(rootfs), "sysdata")
        proot_args.append(f"--bind={sysdata_dir}/sys_empty:/sys/fs/selinux")
        proot_args += fake_proc_bindings(rootfs)
        tmp_dir = os.path.join(rootfs, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        try:
            os.chmod(tmp_dir, 0o1777)
        except OSError:
            pass
        proot_args.append(f"--bind={tmp_dir}:/dev/shm")

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

    try:
        stdin_arg = (
            subprocess.PIPE if stdin_input is not None
            else subprocess.DEVNULL
        )
        proc = subprocess.Popen(
            proot_args,
            env=child_env,
            stdin=stdin_arg,
            start_new_session=True,
        )
        try:
            if stdin_input is not None:
                proc.communicate(input=stdin_input.encode())
            else:
                proc.wait()
        except KeyboardInterrupt:
            try:
                os.killpg(proc.pid, signal.SIGTERM)
            except OSError:
                pass
            proc.wait()
            raise
        return proc.returncode
    except FileNotFoundError as exc:
        raise BuildError(f"proot binary not available: {exc}") from exc


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
        l2s_dir = os.path.join(stage.rootfs_dir, ".l2s")
        os.makedirs(l2s_dir, exist_ok=True)
        env["PROOT_L2S_DIR"] = l2s_dir
    env.pop("LD_PRELOAD", None)
    return env
