# Opt-in live test: real proot execution end-to-end. Skipped unless
# RUN_LIVE_TESTS=1, and additionally skipped when proot is not installed.
#
#   RUN_LIVE_TESTS=1 python -m pytest tests/live/test_live_proot.py -q

import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from proot_distro.api import ProotDistro
from proot_distro.commands.install import command_install
from proot_distro.commands.remove import command_remove
from proot_distro.paths import container_rootfs

pytestmark = pytest.mark.live


@pytest.mark.skipif(shutil.which("proot") is None, reason="proot not installed")
def test_live_proot_run_echo():
    name = "live-proot"
    try:
        command_install(SimpleNamespace(
            image_ref="alpine:latest", custom_container_name=name,
            override_arch=None,
        ))
        assert os.path.isdir(container_rootfs(name))

        # `login` execs proot (replacing the process), so run it in a child.
        # The child inherits this process's sandbox XDG_* env, so it resolves
        # the same container store.
        code = (
            "import sys;"
            "from proot_distro.cli import main;"
            f"sys.argv=['pd','login','{name}','--','echo','PROOT_OK'];"
            "main()"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=os.environ.copy(), timeout=120,
        )
        assert "PROOT_OK" in result.stdout, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )

        # The programmatic API keeps the application process and returns a
        # handle while using the same descriptor-pinned login pipeline.
        process = ProotDistro().start(
            name, ["echo", "API_PROOT_OK"], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = process.communicate(timeout=120)
        assert b"API_PROOT_OK" in stdout, (
            f"stdout={stdout!r} stderr={stderr!r}"
        )
    finally:
        if os.path.isdir(container_rootfs(name)):
            command_remove(SimpleNamespace(target=name, verbose=False))


# --- a RUN step's proot is never the first to die ---------------------------
# The offline versions (tests/security/test_run_step_proot_last.py) use a
# stand-in; these hold the real binary to the behaviour it relies on.
# Real proot SIGKILLed alone leaves its tracees running untraced.

# A step that ignores SIGTERM, with one tracee in a session of its own
# whose parent is still alive when the step is swept.
_STEP = ("trap '' TERM; sleep 120 & "
         "setsid sh -c \"trap '' TERM; exec sleep 120\" & wait")


def _tracees(proot_pid, want, timeout=30):
    """proot's descendants once *want* of them are up, with tracer pids."""
    import time

    deadline = time.monotonic() + timeout
    while True:
        found, todo = {}, [proot_pid]
        while todo:
            pid = todo.pop()
            try:
                with open(f"/proc/{pid}/task/{pid}/children") as fh:
                    children = [int(tok) for tok in fh.read().split()]
            except OSError:
                children = []
            for child in children:
                found[child] = _tracer(child)
                todo.append(child)
        if len(found) >= want or time.monotonic() > deadline:
            return found
        time.sleep(0.05)


def _tracer(pid):
    try:
        with open(f"/proc/{pid}/status") as fh:
            for line in fh:
                if line.startswith("TracerPid:"):
                    return int(line.split()[1])
    except OSError:
        return None
    return None


def _gone(pids, timeout=10):
    import time

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        left = []
        for pid in pids:
            try:
                with open(f"/proc/{pid}/stat") as fh:
                    data = fh.read()
            except OSError:
                continue
            if data[data.rindex(")") + 1:].split()[0] != "Z":
                left.append(pid)
        if not left:
            return []
        time.sleep(0.05)
    return left


def _kill_all(pids):
    import signal as _signal

    for pid in pids:
        try:
            os.kill(pid, _signal.SIGKILL)
        except OSError:
            pass
        try:
            os.waitpid(pid, os.WNOHANG)
        except OSError:
            pass


@pytest.mark.skipif(shutil.which("proot") is None, reason="proot not installed")
def test_live_interrupted_step_ends_every_tracee(monkeypatch):
    from proot_distro.helpers.build_engine import run_step

    if not run_step._become_subreaper():
        pytest.skip("kernel refuses PR_SET_CHILD_SUBREAPER")
    monkeypatch.setattr(run_step, "_STRAY_GRACE_SECONDS", 0.5)
    baseline = set(run_step._adopted())
    proc = subprocess.Popen(
        [shutil.which("proot"), "-r", "/", "sh", "-c", _STEP],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
        preexec_fn=run_step._step_child_setup(None),
    )
    tracees = {}
    try:
        tracees = _tracees(proc.pid, want=3)
        assert len(tracees) >= 3, tracees
        assert set(tracees.values()) == {proc.pid}

        # What _exec_proot does on Ctrl-C.
        run_step._stop_step(proc, baseline, quiet=True)
        run_step._end_proot(proc)

        assert proc.returncode is not None
        assert _gone(list(tracees)) == []
    finally:
        _kill_all(list(tracees))
        if proc.poll() is None:
            proc.kill()
            proc.wait()


_DOOMED_BUILD = r"""
import subprocess, sys, time
sys.path.insert(0, sys.argv[1])
from proot_distro.helpers.build_engine import run_step
proc = subprocess.Popen(
    [sys.argv[2], "-r", "/", "sh", "-c", sys.argv[3]],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    start_new_session=True,
    preexec_fn=run_step._step_child_setup(None),
)
print(proc.pid, flush=True)
time.sleep(120)
"""


@pytest.mark.skipif(shutil.which("proot") is None, reason="proot not installed")
def test_live_killed_build_process_takes_its_step_down():
    import proot_distro

    package_parent = os.path.dirname(os.path.dirname(proot_distro.__file__))
    build = subprocess.Popen(
        [sys.executable, "-I", "-c", _DOOMED_BUILD, package_parent,
         shutil.which("proot"), _STEP],
        stdout=subprocess.PIPE, text=True,
    )
    tracees = {}
    try:
        proot_pid = int(build.stdout.readline())
        tracees = _tracees(proot_pid, want=3)
        assert len(tracees) >= 3, tracees

        build.kill()
        build.wait()

        assert _gone([proot_pid] + list(tracees)) == []
    finally:
        if build.poll() is None:
            build.kill()
            build.wait()
        build.stdout.close()
        _kill_all(list(tracees))
