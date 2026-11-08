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

"""Exceptions raised by the public programmatic API.

Only exceptions exported by :mod:`proot_distro.api` are part of the
stable API. Internal ``OSError`` and protocol-specific exceptions are
translated at this boundary so applications do not need to know how an
operation is implemented.
"""


class ProotDistroError(Exception):
    """Base class for failures reported by the programmatic API."""

    def __init__(self, message: str, *, operation: str = "") -> None:
        super().__init__(message)
        self.operation = operation


class InvalidArgumentError(ProotDistroError):
    """An API argument is missing, malformed, or outside its limits."""


class NetworkError(ProotDistroError):
    """An operation failed while communicating with a remote service."""


class AuthenticationError(ProotDistroError):
    """A registry refused credentials or denied their requested access."""


class NotFoundError(ProotDistroError):
    """A requested container, image, archive, or source does not exist."""


class AlreadyExistsError(ProotDistroError):
    """An operation refused to replace an existing resource."""


class BusyError(ProotDistroError):
    """A resource is locked by another operation or live session."""


class IntegrityError(ProotDistroError):
    """Cached, downloaded, or archived content failed validation."""


class UnsupportedOperationError(ProotDistroError):
    """The requested operation is not supported for this resource."""


class PartialOperationError(ProotDistroError):
    """An operation made progress but could not complete every entry."""


class OperationError(ProotDistroError):
    """An operation failed after its arguments were accepted."""


__all__ = (
    "AuthenticationError",
    "AlreadyExistsError",
    "BusyError",
    "IntegrityError",
    "InvalidArgumentError",
    "NetworkError",
    "NotFoundError",
    "OperationError",
    "PartialOperationError",
    "ProotDistroError",
    "UnsupportedOperationError",
)
