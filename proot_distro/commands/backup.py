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

"""CLI adapter for the shared container backup service."""

import sys

from proot_distro.message import crit_error, log_error
from proot_distro.operation_errors import (
    AlreadyExistsFailure,
    BusyFailure,
    NotFoundFailure,
    OperationFailure,
    UnsupportedFailure,
)
from proot_distro.operations.backup import backup_container
from proot_distro.operations.context import OperationContext


def command_backup(args) -> None:
    """Validate terminal output and invoke the shared backup service."""
    output_path = getattr(args, "output", None)
    output_file = getattr(args, "_api_output_file", None)
    if output_path is None and output_file is None:
        if sys.stdout.isatty():
            crit_error(
                "archive data cannot be printed to console. Please use "
                "option '--output' to specify a file or pipe the output "
                "to another command."
            )
            sys.exit(1)
        output_file = sys.stdout.buffer

    try:
        backup_container(
            args.container_name,
            output_path=output_path,
            output_file=output_file,
            compression=getattr(args, "compression", None),
            context=OperationContext(verbose=getattr(args, "verbose", False)),
        )
    except (
        AlreadyExistsFailure,
        BusyFailure,
        NotFoundFailure,
        UnsupportedFailure,
    ) as exc:
        crit_error(str(exc))
        sys.exit(1)
    except OperationFailure as exc:
        if not getattr(exc, "reported", False):
            log_error(str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)


__all__ = ("command_backup",)
