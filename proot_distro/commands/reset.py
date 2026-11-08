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

"""CLI adapter for the shared OCI-backed container reset service."""

import sys

from proot_distro.commands.install import _print_install_summary
from proot_distro.message import crit_error
from proot_distro.operation_errors import (
    BusyFailure,
    NotFoundFailure,
    OperationFailure,
    UnsupportedFailure,
)
from proot_distro.operations.context import OperationContext
from proot_distro.operations.reset import reset_container


def command_reset(args) -> None:
    """Validate CLI input and invoke shared container reset logic."""
    try:
        result = reset_container(
            args.container_name,
            context=OperationContext(verbose=getattr(args, "verbose", False)),
        )
    except (BusyFailure, NotFoundFailure, UnsupportedFailure) as exc:
        crit_error(str(exc))
        sys.exit(1)
    except OperationFailure as exc:
        if not getattr(exc, "reported", False):
            crit_error(str(exc))
        sys.exit(1)
    except RuntimeError:
        # Install emitted its detailed failure before cleaning partial state.
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)
    _print_install_summary(result.name, result.has_entrypoint)


__all__ = ("command_reset",)
