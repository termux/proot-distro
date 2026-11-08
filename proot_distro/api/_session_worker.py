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
from types import SimpleNamespace

from proot_distro.message import capture_log_events, quote_error, set_quiet


_MAX_REQUEST_BYTES = 1024 * 1024


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
    while data:
        written = os.write(fd, data)
        data = data[written:]


def _args(request: dict) -> SimpleNamespace:
    options = request.get("options")
    if not isinstance(options, dict):
        raise ValueError("session options are not valid")
    return SimpleNamespace(
        container_name=request["container"],
        user=options.get("user", "root"),
        redirect_ports=options.get("redirect_ports", False),
        isolated=options.get("isolated", False),
        minimal=options.get("minimal", False),
        shared_home=options.get("shared_home", False),
        shared_tmp=options.get("shared_tmp", False),
        shared_x11=options.get("shared_x11", False),
        no_link2symlink=options.get("no_link2symlink", False),
        no_sysvipc=options.get("no_sysvipc", False),
        no_kill_on_exit=options.get("no_kill_on_exit", False),
        emulator=options.get("emulator"),
        kernel=options.get("kernel"),
        hostname=options.get("hostname"),
        work_dir=options.get("work_dir"),
        env=list(options.get("env") or []),
        bind=list(options.get("bind") or []),
        detach=False,
        get_proot_cmd=False,
        login_cmd=list(request.get("arguments") or []),
        run_args=list(request.get("arguments") or []),
    )


def main() -> int:
    request_fd = status_fd = None
    logs = []
    try:
        request_fd = _fd_from_env("_PROOT_DISTRO_SESSION_REQUEST_FD")
        status_fd = _fd_from_env("_PROOT_DISTRO_SESSION_STATUS_FD")
        # Success is status-fd EOF: the descriptor closes automatically when
        # command_login execs proot. Setup events and a possible final error
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
            args = _args(request)
            kind = request.get("kind")
            if kind == "login":
                from proot_distro.commands.login import command_login
                command_login(args)
            elif kind == "run":
                from proot_distro.commands.run import command_run
                command_run(args)
            else:
                raise ValueError("session kind is not valid")
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
        except OSError:
            pass
        try:
            os.close(status_fd)
        except OSError:
            pass
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
