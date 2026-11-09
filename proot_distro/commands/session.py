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

"""CLI presentation and execution adapter for prepared sessions."""

import os
import shlex
import sys

from proot_distro.constants import PROGRAM_NAME
from proot_distro.message import C, crit_error, msg, warn
from proot_distro.operation_errors import OperationFailure
from proot_distro.operations.session_prepare import (
    SessionOptions,
    prepare_session,
)
from proot_distro.session_runtime import detach_session, execute_session


def command_session(args, kind):
    """Prepare and execute one CLI login or run request."""
    container_name = getattr(args, "container_name", None)
    arguments = (
        getattr(args, "run_args", ())
        if kind == "run"
        else getattr(args, "login_cmd", ())
    ) or ()
    inspect = bool(getattr(args, "get_proot_cmd", False))
    options = SessionOptions(
        user=getattr(args, "user", "root") or "root",
        environment=tuple(getattr(args, "env", ()) or ()),
        binds=tuple(getattr(args, "bind", ()) or ()),
        work_dir=getattr(args, "work_dir", None),
        redirect_ports=bool(getattr(args, "redirect_ports", False)),
        shared_home=bool(getattr(args, "shared_home", False)),
        shared_tmp=bool(getattr(args, "shared_tmp", False)),
        shared_x11=bool(getattr(args, "shared_x11", False)),
        isolated=bool(getattr(args, "isolated", False)),
        minimal=bool(getattr(args, "minimal", False)),
        no_link2symlink=bool(getattr(args, "no_link2symlink", False)),
        no_sysvipc=bool(getattr(args, "no_sysvipc", False)),
        no_kill_on_exit=bool(getattr(args, "no_kill_on_exit", False)),
        emulator=getattr(args, "emulator", None),
        kernel=getattr(args, "kernel", None),
        hostname=getattr(args, "hostname", None),
    )
    try:
        with prepare_session(
            kind,
            container_name,
            arguments,
            options=options,
            inspect=inspect,
        ) as prepared:
            if inspect:
                warn(
                    f"printed for inspection: this is not a copy of what "
                    f"{PROGRAM_NAME} runs. It names the rootfs, which is "
                    f"resolved afresh whenever the command is run, while the "
                    f"session {PROGRAM_NAME} starts passes '--rootfs=.' from "
                    f"inside the directory it validated."
                )
                _print_command(prepared.launch.shell_command)
                raise SystemExit(0)
            if getattr(args, "detach", False):
                pid = detach_session(
                    prepared,
                    stdout_fd=getattr(args, "_detach_stdout_fd", None),
                    stderr_fd=getattr(args, "_detach_stderr_fd", None),
                )
                _print_detached(prepared.launch, pid)
                return pid
            execute_session(prepared)
    except OperationFailure as exc:
        crit_error(str(exc))
        raise SystemExit(1) from None


def _print_command(text):
    """Write an inspected command with filesystem-byte fidelity."""
    text += "\n"
    try:
        data = os.fsencode(text)
    except UnicodeEncodeError:
        data = text.encode("utf-8", "backslashreplace")
    sys.stdout.flush()
    sys.stdout.buffer.write(data)
    sys.stdout.buffer.flush()


def _print_detached(launch, pid):
    msg()
    msg(f"{C['CYAN']}Started detached session for container "
        f"'{launch.container}':{C['RST']}")
    msg()
    msg(f"{C['CYAN']}PID:     {C['GREEN']}{pid}{C['RST']}")
    msg(f"{C['CYAN']}Command: "
        f"{C['GREEN']}{shlex.join(launch.inner_command)}{C['RST']}")
    msg()
    msg(f"{C['CYAN']}List sessions:  "
        f"{C['GREEN']}{PROGRAM_NAME} ps{C['RST']}")
    msg(f"{C['CYAN']}Stop session:   "
        f"{C['GREEN']}{PROGRAM_NAME} kill {pid}{C['RST']}")
    msg()


__all__ = ("command_session",)
