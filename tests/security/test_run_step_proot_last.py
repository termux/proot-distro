# A RUN step's proot must never die before its tracees do.
#
# proot ignores SIGTERM, SIGINT and SIGHUP, and it sets no
# PTRACE_O_EXITKILL: when it is SIGKILLed its tracees are detached and
# carry on as plain host processes, with no path translation, so a
# step's `rm -rf /x` would then reach the host's /x. Its SIGQUIT handler
# is the safe way out -- it SIGKILLs every tracee, then exits.
#
# Two routes broke that rule:
#
# * An interrupted step (Ctrl-C, an API cancel) was swept with proot as
#   one of the targets, and proot -- this process's own child, so first
#   in the list -- got its SIGKILL before the processes it traced. A
#   tracee in a session of its own, whose parent was still alive at the
#   sweep, was not a target at all and outlived proot untraced.
# * A build process killed outright (kill -9, the OOM killer, an API
#   worker force-killed on a second Ctrl-C) left proot running with
#   nobody to stop it, and the step ran on until it ended by itself.
#
# Real proot cannot be assumed offline, so a stand-in models exactly the
# behaviour above; tests/live/test_live_proot.py repeats both routes
# with the real binary.

import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from proot_distro.helpers.build_engine import run_step
from proot_distro.helpers.build_engine.stage import Stage


# Ignores what proot ignores, kills its children on SIGQUIT the way
# proot's kill_all_tracees2 does, and -- like proot -- leaves them
# running when it is SIGKILLed itself. `inner` stays in its group and
# shrugs off SIGTERM; `hidden` is in a session of its own, so a sweep of
# the group and of this process's adopted children cannot see it.
_STAND_IN = r"""
import os, signal, subprocess, sys, time
for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
    signal.signal(sig, signal.SIG_IGN)
children = [
    subprocess.Popen(["sh", "-c", "exec sleep 120"]),
    subprocess.Popen(["sh", "-c", "exec sleep 120"], start_new_session=True),
]
def quit(*_):
    for child in children:
        if child.returncode is None:
            try:
                os.kill(child.pid, signal.SIGKILL)
            except OSError:
                pass
    os._exit(255)
signal.signal(signal.SIGQUIT, quit)
with open(sys.argv[1] + ".tmp", "w") as fh:
    fh.write(" ".join(str(child.pid) for child in children))
os.rename(sys.argv[1] + ".tmp", sys.argv[1])
for child in children:
    child.wait()
"""


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            data = fh.read()
    except OSError:
        return False
    return data[data.rindex(")") + 1:].split()[0] != "Z"


def _wait_gone(pids, timeout=10):
    deadline = time.monotonic() + timeout
    while any(_alive(pid) for pid in pids) and time.monotonic() < deadline:
        time.sleep(0.05)
    return [pid for pid in pids if _alive(pid)]


def _read_pids(path, timeout=30):
    deadline = time.monotonic() + timeout
    while not os.path.exists(path):
        assert time.monotonic() < deadline, "stand-in never started"
        time.sleep(0.02)
    with open(path) as fh:
        return [int(token) for token in fh.read().split()]


def _cleanup(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
    _wait_gone(pids, timeout=5)
    for pid in pids:
        try:
            os.waitpid(pid, os.WNOHANG)  # adopted here as subreaper
        except OSError:
            pass


@pytest.fixture
def stage(tmp_path):
    stage_dir = tmp_path / "stage-0"
    rootfs = stage_dir / "rootfs"
    rootfs.mkdir(parents=True)
    dir_fd = os.open(str(stage_dir), os.O_RDONLY | os.O_DIRECTORY)
    rootfs_fd = os.open(str(rootfs), os.O_RDONLY | os.O_DIRECTORY)
    built = Stage(index=0, name=None, rootfs_dir=str(rootfs),
                  target_arch_pd="x86_64", dir_fd=dir_fd,
                  rootfs_fd=rootfs_fd)
    try:
        yield built
    finally:
        built.close()


@pytest.fixture
def stand_in(tmp_path):
    script = tmp_path / "stand_in.py"
    script.write_text(_STAND_IN)
    return script


def test_an_interrupted_step_never_sigkills_proot_before_its_tracees(
        stage, stand_in, tmp_path, monkeypatch):
    if not run_step._become_subreaper():
        pytest.skip("kernel refuses PR_SET_CHILD_SUBREAPER")
    monkeypatch.setattr(run_step, "IS_TERMUX", False)
    monkeypatch.setattr(run_step, "get_proot_bin", lambda: "/usr/bin/proot")
    monkeypatch.setattr(run_step, "get_emulator_args", lambda *a: [])
    monkeypatch.setattr(run_step, "get_device_cpu_arch", lambda: "x86_64")
    monkeypatch.setattr(run_step, "_STRAY_GRACE_SECONDS", 0.5)

    pidfile = str(tmp_path / "pids")
    real_popen = subprocess.Popen
    started = {}

    def _popen(_proot_argv, **kwargs):
        # The stand-in in proot's place, started exactly as proot is:
        # same session, same preexec hook, same descriptors.
        kwargs.pop("env", None)
        proc = real_popen([sys.executable, "-I", str(stand_in), pidfile],
                          **kwargs)
        started["proot"] = proc
        return proc

    def _interrupted(proc, _baseline):
        started["tracees"] = _read_pids(pidfile)
        raise KeyboardInterrupt

    sent = []
    real_kill, real_killpg = os.kill, os.killpg

    def _kill(pid, sig):
        sent.append(("kill", pid, sig))
        return real_kill(pid, sig)

    def _killpg(pgid, sig):
        sent.append(("killpg", pgid, sig))
        return real_killpg(pgid, sig)

    monkeypatch.setattr(run_step.subprocess, "Popen", _popen)
    monkeypatch.setattr(run_step, "_wait_for_step", _interrupted)
    monkeypatch.setattr(os, "kill", _kill)
    monkeypatch.setattr(os, "killpg", _killpg)

    engine = SimpleNamespace(quiet=True, verbose=False, emulator="",
                             tmp_root=str(tmp_path))
    try:
        with pytest.raises(KeyboardInterrupt):
            run_step._exec_proot(engine, stage, ["true"], None)
        monkeypatch.undo()
        proot = started["proot"]
        tracees = started["tracees"]

        assert proot.returncode is not None, "proot was left running"
        assert not [entry for entry in sent
                    if entry[1] == proot.pid and entry[2] == signal.SIGKILL]
        # The tracee the sweep could not see went too: proot was ended
        # the way that takes every tracee with it.
        assert ("kill", proot.pid, signal.SIGQUIT) in sent
        assert _wait_gone(tracees) == []
    finally:
        monkeypatch.undo()
        _cleanup(started.get("tracees", []))
        if "proot" in started and started["proot"].poll() is None:
            started["proot"].kill()
            started["proot"].wait()


# Started as the build process would be, then killed outright: the
# kernel must hand proot its SIGQUIT.
_DOOMED_BUILD = r"""
import subprocess, sys, time
sys.path.insert(0, sys.argv[3])
from proot_distro.helpers.build_engine import run_step
proc = subprocess.Popen(
    [sys.executable, "-I", sys.argv[1], sys.argv[2]],
    start_new_session=True,
    preexec_fn=run_step._step_child_setup(None),
)
print(proc.pid, flush=True)
time.sleep(120)
"""


def test_a_killed_build_process_takes_its_step_down(stand_in, tmp_path):
    pidfile = str(tmp_path / "pids")
    package_parent = os.path.dirname(os.path.dirname(run_step.__file__))
    package_parent = os.path.dirname(os.path.dirname(package_parent))
    build = subprocess.Popen(
        [sys.executable, "-I", "-c", _DOOMED_BUILD, str(stand_in), pidfile,
         package_parent],
        stdout=subprocess.PIPE, text=True,
    )
    tracees = []
    proot = None
    try:
        proot = int(build.stdout.readline())
        tracees = _read_pids(pidfile)
        assert all(_alive(pid) for pid in tracees + [proot])

        build.kill()
        build.wait()

        assert _wait_gone([proot] + tracees) == []
    finally:
        if build.poll() is None:
            build.kill()
            build.wait()
        build.stdout.close()
        _cleanup(tracees + ([proot] if proot else []))


def test_a_step_whose_parent_is_already_gone_never_starts(tmp_path):
    # The death signal is armed in the child after the fork. A parent
    # that died before that would never send it, so the child must see
    # that and not exec at all.
    marker = tmp_path / "started"
    hook = run_step._step_child_setup(None, parent=os.getppid())
    result = subprocess.run(
        [sys.executable, "-I", "-c",
         f"open({str(marker)!r}, 'w').close()"],
        preexec_fn=hook, timeout=60,
    )
    assert result.returncode == 1
    assert not marker.exists()


def test_the_sweep_leaves_a_reaped_proots_group_alone(monkeypatch):
    # Once proot is reaped its pid -- the group's id -- can belong to an
    # unrelated process group, which a group scan would then signal.
    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()
    scanned = []
    monkeypatch.setattr(run_step, "_group_members",
                        lambda pgid, skip=(): scanned.append(pgid) or [])
    run_step._stop_step(proc, set(run_step._adopted()), quiet=True)
    assert scanned == []

    live = subprocess.Popen(["sleep", "30"], start_new_session=True)
    try:
        run_step._stop_step(live, set(run_step._adopted()), quiet=True)
        assert scanned == [live.pid]
        assert live.poll() is None  # proot itself is never a target
    finally:
        live.kill()
        live.wait()
