import io
import os

import pytest

from proot_distro import _detach_worker
from proot_distro.session_errors import SessionExecutionFailure


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
