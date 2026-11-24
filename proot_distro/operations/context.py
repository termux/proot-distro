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

"""Explicit event and presentation dependencies for shared operations."""

from dataclasses import dataclass
from typing import Callable, Optional

from proot_distro.message import log_error, log_info, warn


EventEmitter = Callable[
    [str, str, Optional[str], Optional[str]], None
]


def _message_emitter(kind, message, phase, resource) -> None:
    """Bridge service events to existing CLI/API-worker message routing."""
    if kind == "warning":
        warn(message, phase=phase, resource=resource)
    elif kind == "error":
        log_error(message, phase=phase, resource=resource)
    else:
        log_info(message, phase=phase, resource=resource)


@dataclass(frozen=True)
class OperationContext:
    """Dependencies and presentation preferences for one service call."""

    verbose: bool = False
    emit: EventEmitter = _message_emitter

    def info(
        self, message: str, *, phase: Optional[str] = None,
        resource: Optional[str] = None,
    ) -> None:
        self.emit("info", message, phase, resource)

    def warning(
        self, message: str, *, phase: Optional[str] = None,
        resource: Optional[str] = None,
    ) -> None:
        self.emit("warning", message, phase, resource)

    def error(
        self, message: str, *, phase: Optional[str] = None,
        resource: Optional[str] = None,
    ) -> None:
        self.emit("error", message, phase, resource)


__all__ = ("EventEmitter", "OperationContext")
