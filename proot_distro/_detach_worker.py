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

"""Single-threaded exec boundary for detached-session daemonization.

An embedding application can have arbitrary threads, so it must not call
``os.fork()`` and continue through Python code in the child. The parent execs
this module through the isolated private bootstrap first. This fresh
interpreter has one thread and may safely perform the traditional double-fork
while preserving explicitly passed rootfs, container-lock, and output
descriptors.
"""

import json
import os
import sys

from proot_distro.session_errors import SessionExecutionFailure
from proot_distro.session_runtime import (
    _MAX_DETACH_REQUEST_BYTES,
    _MAX_DETACH_RESPONSE_BYTES,
    _spawn_detached_local,
)


def _read_request():
    raw = sys.stdin.buffer.read(_MAX_DETACH_REQUEST_BYTES + 1)
    if len(raw) > _MAX_DETACH_REQUEST_BYTES:
        raise SessionExecutionFailure(
            "detached-session request exceeds its size limit."
        )
    try:
        request = json.loads(raw)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise SessionExecutionFailure(
            "detached-session launcher received an invalid request."
        ) from exc
    if not isinstance(request, dict):
        raise SessionExecutionFailure(
            "detached-session launcher received an invalid request."
        )
    return request


def _string_list(value, label):
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise SessionExecutionFailure(
            f"detached-session {label} must be a list of strings."
        )
    return value


def _string_mapping(value, label):
    if not isinstance(value, dict) or not all(
        isinstance(key, str) and isinstance(item, str)
        for key, item in value.items()
    ):
        raise SessionExecutionFailure(
            f"detached-session {label} must map strings to strings."
        )
    return value


def _descriptor(value, label):
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 2:
        raise SessionExecutionFailure(
            f"detached-session {label} descriptor is invalid."
        )
    try:
        os.fstat(value)
    except OSError as exc:
        raise SessionExecutionFailure(
            f"detached-session {label} descriptor is not open."
        ) from exc
    return value


def _request_parts(request):
    expected = {
        "proot_bin", "proot_args", "child_env", "register_kwargs",
        "rootfs_fd", "lock_fd", "stdout_fd", "stderr_fd",
    }
    if set(request) != expected:
        raise SessionExecutionFailure(
            "detached-session launcher received an invalid request."
        )
    proot_bin = request.get("proot_bin")
    if not isinstance(proot_bin, str) or not proot_bin:
        raise SessionExecutionFailure(
            "detached-session executable must be a non-empty string."
        )
    proot_args = _string_list(request.get("proot_args"), "arguments")
    child_env = _string_mapping(request.get("child_env"), "environment")
    register_kwargs = request.get("register_kwargs")
    if not isinstance(register_kwargs, dict):
        raise SessionExecutionFailure(
            "detached-session registration must be an object."
        )
    rootfs_fd = _descriptor(request.get("rootfs_fd"), "rootfs")
    lock_fd = _descriptor(request.get("lock_fd"), "lock")
    stdout_fd = _descriptor(request.get("stdout_fd"), "stdout")
    stderr_fd = _descriptor(request.get("stderr_fd"), "stderr")
    return (
        proot_bin,
        proot_args,
        child_env,
        register_kwargs,
        rootfs_fd,
        lock_fd,
        stdout_fd,
        stderr_fd,
    )


def _write_response(response) -> None:
    raw = json.dumps(
        response, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii")
    if len(raw) > _MAX_DETACH_RESPONSE_BYTES:
        raw = b'{"error":"detached-session launcher error is too large."}'
    sys.stdout.buffer.write(raw)
    sys.stdout.buffer.flush()


def main() -> int:
    try:
        (
            proot_bin,
            proot_args,
            child_env,
            register_kwargs,
            rootfs_fd,
            lock_fd,
            stdout_fd,
            stderr_fd,
        ) = _request_parts(_read_request())

        # pass_fds makes descriptors survive the launcher exec. Only the
        # container lock must survive the following proot exec; rootfs and log
        # descriptors are consumed before it.
        for fd in {rootfs_fd, stdout_fd, stderr_fd} - {None, lock_fd}:
            os.set_inheritable(fd, False)
        if lock_fd is not None:
            os.set_inheritable(lock_fd, True)

        pid = _spawn_detached_local(
            proot_bin,
            proot_args,
            child_env,
            register_kwargs=register_kwargs,
            rootfs_fd=rootfs_fd,
            stdout_fd=stdout_fd,
            stderr_fd=stderr_fd,
        )
    except SessionExecutionFailure as exc:
        _write_response({"error": str(exc)})
        return 1
    except BaseException:
        _write_response({
            "error": "detached-session launcher failed unexpectedly."
        })
        return 1

    _write_response({"pid": pid})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
