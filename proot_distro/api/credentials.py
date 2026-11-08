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

"""Explicit registry authentication for the public API."""

from dataclasses import dataclass, field
import os
from typing import Callable, Optional

from proot_distro.api.errors import InvalidArgumentError


_MAX_CREDENTIAL_BYTES = 128 * 1024


def _credential_text(value, name: str, *, allow_colon: bool) -> str:
    if not isinstance(value, str) or not value:
        raise InvalidArgumentError(
            f"{name} must be a non-empty string.", operation="configure"
        )
    if "\0" in value:
        raise InvalidArgumentError(
            f"{name} must not contain a NUL byte.", operation="configure"
        )
    if not allow_colon and ":" in value:
        raise InvalidArgumentError(
            f"{name} must not contain a colon.", operation="configure"
        )
    try:
        os.fsencode(value)
    except UnicodeError:
        raise InvalidArgumentError(
            f"{name} cannot be represented by the host.",
            operation="configure",
        ) from None
    return value


@dataclass(frozen=True)
class RegistryCredentials:
    """A registry username and password or access token.

    The secret is excluded from ``repr()`` so logs and debugger displays do
    not reveal it. A colon is allowed in the password but not the username,
    matching HTTP Basic authentication's ``username:password`` encoding.
    """

    username: str
    password: str = field(repr=False)

    def __post_init__(self) -> None:
        username = _credential_text(
            self.username, "username", allow_colon=False
        )
        password = _credential_text(
            self.password, "password", allow_colon=True
        )
        if len(os.fsencode(f"{username}:{password}")) > _MAX_CREDENTIAL_BYTES:
            raise InvalidArgumentError(
                "registry credentials are too large.", operation="configure"
            )


@dataclass(frozen=True)
class CredentialRequest:
    """Registry identity supplied to a credential provider."""

    registry: str
    repository: str
    operation: str


CredentialProvider = Callable[
    [CredentialRequest], Optional[RegistryCredentials]
]


__all__ = (
    "CredentialProvider",
    "CredentialRequest",
    "RegistryCredentials",
)
