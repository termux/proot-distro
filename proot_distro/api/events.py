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

"""Optional events emitted by programmatic operations."""

from dataclasses import dataclass
from enum import Enum


class EventKind(str, Enum):
    """Kinds of informational events an operation may emit."""

    RETRY = "retry"


@dataclass(frozen=True)
class OperationEvent:
    """An informational event produced while an operation is running.

    Event messages are intended for display and are not a machine-readable
    error interface. Failures are represented by the documented exception
    classes instead.
    """

    operation: str
    kind: EventKind
    message: str


__all__ = ("EventKind", "OperationEvent")
