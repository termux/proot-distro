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

"""Configured child that becomes a foreground proot session."""

import json
import os
import sys

from proot_distro.message import capture_log_events, quote_error, set_quiet


_MAX_REQUEST_BYTES = 1024 * 1024
_MAX_STATUS_BYTES = 64 * 1024
_OVERSIZED_STATUS_ERROR = "session setup failed: status message is too large"


def _fd_from_env(name: str) -> int:
    raw = os.environ.pop(name, "")
    if not raw.isascii() or not raw.isdigit():
        raise ValueError(f"{name} is not a valid descriptor")
    return int(raw)


def _read_request(fd: int) -> dict:
    chunks = []
    size = 0
    while True:
        chunk = os.read(fd, min(65536, _MAX_REQUEST_BYTES + 1 - size))
        if not chunk:
            break
        chunks.append(chunk)
        size += len(chunk)
        if size > _MAX_REQUEST_BYTES:
            raise ValueError("session request is too large")
    request = json.loads(b"".join(chunks))
    if not isinstance(request, dict):
        raise ValueError("session request must be an object")
    return request


def _write_frame(fd: int, payload: dict) -> None:
    """Write one newline-delimited startup protocol frame."""
    data = json.dumps(
        payload, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii") + b"\n"
    if len(data) > _MAX_STATUS_BYTES:
        raise ValueError("session startup status is too large")
    while data:
        written = os.write(fd, data)
        data = data[written:]


def _request_parts(request: dict):
    """Validate and normalize the private foreground-worker request."""
    options = request.get("options")
    if not isinstance(options, dict):
        raise ValueError("session options are not valid")
    kind = request.get("kind")
    container = request.get("container")
    arguments = request.get("arguments")
    if kind not in ("login", "run") or not isinstance(container, str):
        raise ValueError("session request is not valid")
    if not isinstance(arguments, list):
        raise ValueError("session arguments are not valid")
    return kind, container, arguments, options


def main() -> int:
    request_fd = status_fd = None
    logs = []
    try:
        request_fd = _fd_from_env("_PROOT_DISTRO_SESSION_REQUEST_FD")
        status_fd = _fd_from_env("_PROOT_DISTRO_SESSION_STATUS_FD")
        # Success is status-fd EOF: the descriptor closes automatically when
        # the prepared-session executor execs proot. Setup events and an error
        # are newline-delimited JSON frames written before that handshake.
        os.set_inheritable(status_fd, False)
        request = _read_request(request_fd)
        os.close(request_fd)
        request_fd = None
        os.umask(0o022)
        set_quiet(True)

        def record(kind, text, phase, resource):
            logs.append((kind, text))
            if kind in ("info", "warning"):
                _write_frame(status_fd, {
                    "event": {
                        "kind": kind,
                        "message": text,
                        "phase": phase or "session-setup",
                        "resource": resource or request.get("container"),
                    },
                })

        with capture_log_events(record):
            from proot_distro.operations.session_prepare import prepare_session
            from proot_distro.session_runtime import execute_session

            kind, container, arguments, options = _request_parts(request)
            with prepare_session(
                kind, container, arguments, options=options
            ) as prepared:
                execute_session(prepared)
        raise RuntimeError("session command returned without executing proot")
    except SystemExit as exc:
        messages = [text for kind, text in logs if kind == "error"]
        message = messages[-1] if messages else f"session setup exited: {exc.code}"
    except BaseException as exc:
        message = f"session setup failed: {quote_error(exc)}"
    finally:
        if request_fd is not None:
            try:
                os.close(request_fd)
            except OSError:
                pass

    if status_fd is not None:
        try:
            _write_frame(status_fd, {"error": message})
        except ValueError:
            try:
                _write_frame(status_fd, {"error": _OVERSIZED_STATUS_ERROR})
            except OSError:
                pass
        except OSError:
            pass
        try:
            os.close(status_fd)
        except OSError:
            pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
