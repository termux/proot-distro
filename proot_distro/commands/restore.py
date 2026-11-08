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

"""CLI adapter for the shared container restore service."""

import sys

from proot_distro.commands.help import HELP_COMMANDS
from proot_distro.message import crit_error, log_error, msg
from proot_distro.operation_errors import (
    BusyFailure,
    IntegrityFailure,
    NotFoundFailure,
    OperationFailure,
    UnsupportedFailure,
)
from proot_distro.operations.context import OperationContext
from proot_distro.operations.restore import restore_container


def command_restore(args):
    """Validate terminal input and invoke the shared restore service."""
    archive = getattr(args, "archive", None)
    archive_file = getattr(args, "_api_archive_file", None)
    if archive is None and archive_file is None:
        if sys.stdin.isatty():
            msg()
            crit_error(
                "archive file path is not specified and nothing is being "
                "piped via stdin."
            )
            HELP_COMMANDS["restore"]()
            sys.exit(1)
        archive_file = sys.stdin.buffer

    try:
        result = restore_container(
            archive_path=archive,
            archive_file=archive_file,
            context=OperationContext(verbose=getattr(args, "verbose", False)),
        )
    except (NotFoundFailure, UnsupportedFailure) as exc:
        crit_error(str(exc))
        sys.exit(1)
    except (BusyFailure, IntegrityFailure):
        sys.exit(1)
    except OperationFailure as exc:
        log_error(str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)
    return result.container_name


__all__ = ("command_restore",)
