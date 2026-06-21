# Integration test for proot_distro.commands.login.detach.spawn_detached.
#
# Exercises the real double-fork daemonization end to end using a benign
# `sleep` process in place of proot: the daemon must register itself in
# the session registry (with the GRANDCHILD pid, since register_session
# runs there), keep the entry alive via the inherited flock across exec,
# and have the entry self-pruned by `ps` once it is killed.

import os
import shutil
import signal
import time

import pytest

from proot_distro import session
from proot_distro.commands.login.detach import spawn_detached

pytestmark = pytest.mark.skipif(
    shutil.which("sleep") is None, reason="needs the `sleep` binary"
)


def _wait_for(predicate, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = predicate()
        if val:
            return val
        time.sleep(0.02)
    return predicate()


def _find(pid):
    return next(
        (s for s in session.active_sessions() if s.get("pid") == pid), None
    )


def test_spawn_detached_registers_then_prunes_on_kill():
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    pid = spawn_detached(
        "sleep", ["sleep", "30"], env,
        register_kwargs=dict(
            container="ubuntu", kind="run",
            command_argv=["sleep", "30"], user="root", detach=True,
        ),
    )
    assert isinstance(pid, int) and pid > 0

    try:
        rec = _wait_for(lambda: _find(pid))
        assert rec is not None
        assert rec["container"] == "ubuntu"
        assert rec["kind"] == "run"
        assert rec["detach"] is True
        # The reported PID is a real, live process (the exec'd `sleep`).
        os.kill(pid, 0)
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    # Death drops the inherited flock; the next listing prunes the entry.
    assert _wait_for(lambda: _find(pid) is None) is True
