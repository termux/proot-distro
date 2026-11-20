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

"""CLI presentation adapter for shared tracked-session signalling."""

import sys

from proot_distro.message import crit_error, msg
from proot_distro.operations.context import OperationContext
from proot_distro.operations.errors import (
    SessionSignalFailure,
    SessionSignalValidationFailure,
)
from proot_distro.operations.session_signals import signal_sessions


def command_kill(args) -> None:
    """Translate CLI arguments and signal matching tracked sessions."""
    try:
        signal_sessions(
            getattr(args, "target", None),
            all_sessions=bool(getattr(args, "all", False)),
            signal_name=getattr(args, "signal", None),
            context=OperationContext(
                verbose=bool(getattr(args, "verbose", False))
            ),
        )
    except SessionSignalValidationFailure as exc:
        if exc.show_help:
            _fail(str(exc))
        crit_error(str(exc))
        sys.exit(1)
    except SessionSignalFailure as exc:
        crit_error(str(exc))
        sys.exit(1)


def _fail(message) -> None:
    """Print an error with the kill help page and exit non-zero."""
    from proot_distro.commands.help import HELP_COMMANDS

    msg()
    crit_error(message)
    if "kill" in HELP_COMMANDS:
        HELP_COMMANDS["kill"]()
    msg()
    sys.exit(1)


__all__ = ("command_kill",)
