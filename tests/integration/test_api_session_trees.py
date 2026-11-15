"""SessionProcess signalling against real child process trees."""

import os
import subprocess
import time

import pytest

from proot_distro.api import (
    OperationError,
    ProotDistro,
    RuntimeConfig,
)

_FAKE_PROOT = """#!/bin/sh
# Stands in for proot: starts a grandchild that outlives a plain kill of
# this process, optionally making the whole tree ignore SIGTERM.
if [ "$FAKE_TREE_IGNORE_TERM" = 1 ]; then
  trap '' TERM
fi
sh -c 'exec sleep 300' &
echo $! > "$FAKE_TREE_PIDFILE"
wait
"""


@pytest.fixture
def fake_proot(tmp_path):
    path = tmp_path / "fake-proot"
    path.write_text(_FAKE_PROOT)
    path.chmod(0o755)
    return path


def _program(fake_proot):
    default = RuntimeConfig.default()
    return ProotDistro(RuntimeConfig(
        default.runtime_dir, default.cache_dir, fake_proot
    ))


def _alive(pid):
    try:
        with open(f"/proc/{pid}/stat") as fh:
            return fh.read().rsplit(")", 1)[1].split()[0] != "Z"
    except OSError:
        return False


def _wait_for(predicate, timeout=10.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.05)
    return predicate()


def _grandchild(pidfile):
    assert _wait_for(lambda: pidfile.exists() and pidfile.read_text().strip())
    return int(pidfile.read_text())


def _start(builders, fake_proot, tmp_path, name, ignore_term=False):
    builders.make_container(name)
    pidfile = tmp_path / f"{name}.pid"
    process = _program(fake_proot).start(
        name,
        environment={
            "FAKE_TREE_PIDFILE": str(pidfile),
            "FAKE_TREE_IGNORE_TERM": "1" if ignore_term else "0",
        },
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return process, _grandchild(pidfile)


def test_terminate_stops_grandchildren_too(builders, fake_proot, tmp_path):
    process, grandchild = _start(builders, fake_proot, tmp_path, "tree1")
    try:
        assert _alive(grandchild)
        result = process.terminate()

        assert process.pid in result.pids
        assert process.wait(timeout=10) is not None
        assert _wait_for(lambda: not _alive(grandchild))
    finally:
        process.kill()
        process.wait()


def test_close_escalates_to_kill_for_a_tree_that_ignores_sigterm(
        builders, fake_proot, tmp_path):
    process, grandchild = _start(
        builders, fake_proot, tmp_path, "stubborn", ignore_term=True
    )

    start = time.monotonic()
    code = process.close(timeout=1.0)

    assert code is not None
    assert process.poll() is not None
    assert _wait_for(lambda: not _alive(grandchild))
    # Waited roughly the grace period, then killed; never hung.
    assert time.monotonic() - start < 8


def test_signalling_a_reaped_session_touches_nothing(
        builders, fake_proot, tmp_path, monkeypatch):
    process, grandchild = _start(builders, fake_proot, tmp_path, "reaped")
    process.kill()
    process.wait()
    assert _wait_for(lambda: not _alive(grandchild))

    def forbidden(*_args, **_kwargs):
        raise AssertionError("signalled a PID that may have been reused")

    monkeypatch.setattr(process._owner, "kill_session", forbidden)
    for signal_ in (process.terminate, process.kill):
        result = signal_()
        assert result.pids == ()


def test_failed_tree_lookup_still_signals_the_owned_child(
        builders, fake_proot, tmp_path, monkeypatch):
    process, grandchild = _start(builders, fake_proot, tmp_path, "fallback")

    def broken(*_args, **_kwargs):
        raise OperationError("signalling worker failed", operation="kill")

    monkeypatch.setattr(process._owner, "kill_session", broken)
    try:
        with pytest.raises(OperationError):
            process.kill()
        # The error still reaches the caller, but the child was not left
        # running because of it.
        assert process.wait(timeout=10) is not None
    finally:
        os.kill(grandchild, 9) if _alive(grandchild) else None
