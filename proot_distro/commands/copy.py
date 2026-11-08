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

"""CLI adapter for shared copy and move operation services."""

import sys

from proot_distro.message import crit_error, log_error
from proot_distro.operation_errors import (
    BusyFailure,
    NotFoundFailure,
    OperationFailure,
)
from proot_distro.operations.context import OperationContext
from proot_distro.operations.copy import copy_path, move_path
from proot_distro.operations.errors import (
    TransferPartialFailure,
    TransferValidationFailure,
)


# Transitional internal name retained for exception classification by older
# worker integrations. It is not part of the public API.
CopyPartialError = TransferPartialFailure


def command_copy(args) -> None:
    """Validate CLI preferences and invoke shared copy or move logic."""
    context = OperationContext(verbose=getattr(args, "verbose", False))
    try:
        if getattr(args, "move", False):
            move_path(args.source, args.destination, context=context)
        else:
            copy_path(
                args.source,
                args.destination,
                recursive=getattr(args, "recursive", False),
                context=context,
            )
    except TransferPartialFailure:
        sys.exit(1)
    except (BusyFailure, NotFoundFailure, TransferValidationFailure) as exc:
        crit_error(str(exc))
        sys.exit(1)
    except OperationFailure as exc:
        if not getattr(exc, "reported", False):
            log_error(str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)


__all__ = ("command_copy",)
