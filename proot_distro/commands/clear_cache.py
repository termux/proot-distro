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

"""CLI presentation adapter for the shared cache-cleanup operation."""

import sys

from proot_distro.message import crit_error
from proot_distro.operation_errors import (
    BusyFailure, IntegrityFailure, OperationFailure,
)
from proot_distro.operations import OperationContext, clear_cache


def command_clear_cache(args) -> None:
    """Empty the cache or sweep layers selected by CLI flags."""
    try:
        clear_cache(
            orphan=getattr(args, "orphan", False),
            build_cache=getattr(args, "build_cache", False),
            context=OperationContext(verbose=getattr(args, "verbose", False)),
        )
    except (BusyFailure, IntegrityFailure, OperationFailure) as exc:
        crit_error(str(exc))
        sys.exit(1)


__all__ = ("command_clear_cache",)
