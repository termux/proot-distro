import io
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from proot_distro import _detach_worker
from proot_distro.session_errors import SessionExecutionFailure

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(
    _detach_worker.__file__
)))


def _run_launcher(prelude, request, timeout=30):
    """Run _detach_worker.main() in a fresh interpreter after *prelude*.

    The double fork must never happen inside pytest itself: a broken
    child would carry on as a second copy of the test run.
    """
    script = (
        f"import os, signal, sys\nsys.path.insert(0, {_ROOT!r})\n"
        f"{prelude}\n"
        "from proot_distro import _detach_worker\n"
        "raise SystemExit(_detach_worker.main())\n"
    )
    return subprocess.run(
        [sys.executable, "-I", "-c", script],
        input=json.dumps(request).encode(),
        capture_output=True, timeout=timeout,
    )


def _request(proot_args):
    return {
        "proot_bin": proot_args[0],
        "proot_args": proot_args,
        "child_env": {"PATH": "/usr/bin:/bin"},
        "register_kwargs": {},
        "rootfs_fd": None,
        "lock_fd": None,
        "stdout_fd": None,
        "stderr_fd": None,
    }


def test_a_failing_fork_child_never_writes_a_second_reply():
    # Before setsid() the middle child is still in the launcher's process
    # group, where a cancel's SIGINT lands. Only OSError was caught there,
    # so a KeyboardInterrupt unwound into the launcher's main() inside the
    # forked copy, which then wrote its own reply after the real one.
    result = _run_launcher(
        "def _interrupted():\n    raise KeyboardInterrupt\n"
        "os.setsid = _interrupted\n",
        _request(["/bin/true"]),
    )

    assert result.returncode == 1
    reply = json.loads(result.stdout)
    assert set(reply) == {"error"}


@pytest.mark.parametrize("launcher_sigint,expect_ignored", [
    ("", False),
    ("signal.signal(signal.SIGINT, signal.SIG_IGN)", True),
])
def test_daemon_starts_with_the_sigint_disposition_it_was_given(
        tmp_path, launcher_sigint, expect_ignored):
    # The launcher ignores SIGINT while it spawns; that must not leak into
    # the session, while a caller's own "ignore" is inherited as exec
    # would inherit it.
    status = tmp_path / "status"
    result = _run_launcher(
        "import proot_distro.session_runtime as runtime\n"
        "runtime.register_session = lambda **_kwargs: None\n"
        f"{launcher_sigint}",
        _request([
            "/bin/sh", "-c",
            f"grep '^SigIgn:' /proc/self/status > '{status}.tmp' && "
            f"mv '{status}.tmp' '{status}'",
        ]),
    )
    assert result.returncode == 0, result.stdout
    assert json.loads(result.stdout)["pid"] > 0

    deadline = time.monotonic() + 10
    while not status.exists() and time.monotonic() < deadline:
        time.sleep(0.05)
    mask = int(status.read_text().split()[1], 16)
    assert bool(mask & (1 << (signal.SIGINT - 1))) is expect_ignored


class _Input:
    def __init__(self, payload):
        self.buffer = io.BytesIO(payload)


@pytest.mark.parametrize("payload", [
    b"",
    b"not json",
    b"[]",
    b"[" * 2000 + b"]" * 2000,
])
def test_detach_worker_rejects_malformed_request(monkeypatch, payload):
    monkeypatch.setattr(_detach_worker.sys, "stdin", _Input(payload))

    with pytest.raises(SessionExecutionFailure, match="invalid request"):
        _detach_worker._read_request()


def test_detach_worker_bounds_request_before_decoding(monkeypatch):
    monkeypatch.setattr(_detach_worker, "_MAX_DETACH_REQUEST_BYTES", 4)
    monkeypatch.setattr(_detach_worker.sys, "stdin", _Input(b"{}   "))

    with pytest.raises(SessionExecutionFailure, match="size limit"):
        _detach_worker._read_request()


@pytest.mark.parametrize("field,value", [
    ("proot_args", "not-a-list"),
    ("proot_args", [1]),
    ("child_env", []),
    ("child_env", {"KEY": 1}),
    ("register_kwargs", []),
    ("rootfs_fd", True),
    ("lock_fd", -1),
    ("stdout_fd", 1),
])
def test_detach_worker_rejects_invalid_request_fields(field, value):
    request = {
        "proot_bin": "proot",
        "proot_args": ["proot"],
        "child_env": {},
        "register_kwargs": {},
        "rootfs_fd": None,
        "lock_fd": None,
        "stdout_fd": None,
        "stderr_fd": None,
    }
    request[field] = value

    with pytest.raises(SessionExecutionFailure):
        _detach_worker._request_parts(request)


def test_detach_worker_rejects_unknown_request_field():
    request = {
        "proot_bin": "proot",
        "proot_args": ["proot"],
        "child_env": {},
        "register_kwargs": {},
        "rootfs_fd": None,
        "lock_fd": None,
        "stdout_fd": None,
        "stderr_fd": None,
        "unexpected": True,
    }

    with pytest.raises(SessionExecutionFailure, match="invalid request"):
        _detach_worker._request_parts(request)


def test_detach_worker_accepts_open_descriptor_roles():
    read_fd, write_fd = os.pipe()
    try:
        parts = _detach_worker._request_parts({
            "proot_bin": "proot",
            "proot_args": ["proot"],
            "child_env": {"KEY": "value"},
            "register_kwargs": {"container": "box"},
            "rootfs_fd": read_fd,
            "lock_fd": write_fd,
            "stdout_fd": None,
            "stderr_fd": None,
        })
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert parts[0] == "proot"
    assert parts[1] == ["proot"]
    assert parts[2] == {"KEY": "value"}
    assert parts[4:6] == (read_fd, write_fd)
