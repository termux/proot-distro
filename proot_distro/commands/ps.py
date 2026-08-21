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

# Architecture: Lists active proot sessions reported by the session
# registry (session.py). active_sessions() already prunes dead entries
# and validates every field it hands back, so this module only formats
# output. --quiet prints one PID per line (to stdout, for piping into
# kill/xargs); the default renders a colored
# PID/CONTAINER/TYPE/USER/UPTIME/COMMAND table to stderr, mirroring the
# style of command_list.
#
# Two of those columns are free text: the `--user` the session was
# started with and the argv it runs. Both are read back out of
# SESSIONS_DIR, which is guest-writable on Termux, and a guest can write
# a record for a PID of its own -- so an ESC in either would repaint the
# terminal of whoever runs `ps`. They go through quote_path, the same
# rule every name read off a filesystem follows. CONTAINER and TYPE need
# no such treatment: the registry only reports a container name this
# program would accept and a kind out of a closed vocabulary.

import shlex
import time

from proot_distro.constants import PROGRAM_NAME
from proot_distro.message import C, msg, quote_path, terminal_width
from proot_distro.session import active_sessions

# Fixed columns (everything except the trailing, space-filling COMMAND).
_HEADERS = ("PID", "CONTAINER", "TYPE", "USER", "UPTIME", "COMMAND")
_GAP = 2


def command_ps(args) -> None:
    """List every active container session (one row per live proot)."""
    quiet = getattr(args, "quiet", False)
    sessions = active_sessions()

    if quiet:
        for sess in sessions:
            print(sess["pid"])
        return

    msg()
    if not sessions:
        msg(f"{C['YELLOW']}No active sessions.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Start one with: "
            f"{C['GREEN']}{PROGRAM_NAME} login <name>{C['RST']}")
        msg()
        return

    now = time.time()
    any_detached = any(s["detach"] for s in sessions)
    rows = [
        (
            str(sess["pid"]),
            sess["container"],
            sess["kind"] + ("*" if sess["detach"] else ""),
            quote_path(sess["user"]),
            _fmt_uptime(now - sess["start_time"]),
            _fmt_command(sess["command"]),
        )
        for sess in sessions
    ]

    # Fixed-column widths are content-driven; COMMAND takes the rest of
    # the terminal width and is truncated if it overflows.
    widths = [
        max(len(_HEADERS[i]), max(len(r[i]) for r in rows))
        for i in range(len(_HEADERS) - 1)
    ]
    used = sum(widths) + _GAP * len(widths)
    cmd_width = max(len(_HEADERS[-1]), terminal_width() - used)

    pad = " " * _GAP
    head = [_HEADERS[i].ljust(widths[i]) for i in range(len(widths))]
    head.append(_HEADERS[-1])
    msg(f"{C['UBCYAN']}{pad.join(head)}{C['RST']}")

    for r in rows:
        cmd = r[-1]
        if len(cmd) > cmd_width:
            cmd = cmd[: max(1, cmd_width - 1)] + "…"
        cells = [
            f"{C['CYAN']}{r[0].ljust(widths[0])}{C['RST']}",
            f"{C['GREEN']}{r[1].ljust(widths[1])}{C['RST']}",
            f"{C['CYAN']}{r[2].ljust(widths[2])}{C['RST']}",
            f"{C['CYAN']}{r[3].ljust(widths[3])}{C['RST']}",
            f"{C['CYAN']}{r[4].ljust(widths[4])}{C['RST']}",
            f"{C['CYAN']}{cmd}{C['RST']}",
        ]
        msg(pad.join(cells))

    if any_detached:
        msg()
        msg(f"{C['YELLOW']}* detached session{C['RST']}")
    msg()


def _fmt_uptime(seconds: float) -> str:
    """Compact human-readable elapsed time (e.g. '0m44s', '1h04m', '2d03h')."""
    s = int(seconds) if seconds > 0 else 0
    if s < 3600:
        return f"{s // 60}m{s % 60:02d}s"
    if s < 86400:
        return f"{s // 3600}h{(s % 3600) // 60:02d}m"
    return f"{s // 86400}d{(s % 86400) // 3600:02d}h"


def _fmt_command(command) -> str:
    """Render the recorded inner argv as a single shell-style string.

    shlex.join() quotes for a shell, which says nothing about a control
    character: it wraps an ESC in single quotes and passes it through.
    quote_path is what makes the result printable.
    """
    return quote_path(shlex.join(command))


__all__ = ("command_ps",)
