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

"""Configuration for independent PRoot-Distro API instances."""

from dataclasses import dataclass
import os
from typing import Optional, Union

from proot_distro.api.errors import InvalidArgumentError


_PathArgument = Union[str, os.PathLike[str]]


def _directory(value, name: str) -> str:
    """Normalize a configured trust root or raise a public error."""
    try:
        path = os.fspath(value)
    except TypeError:
        raise InvalidArgumentError(
            f"{name} must be a string or path-like object.",
            operation="configure",
        ) from None
    if not isinstance(path, str) or not path:
        raise InvalidArgumentError(
            f"{name} must be a non-empty text path.", operation="configure"
        )
    if "\0" in path:
        raise InvalidArgumentError(
            f"{name} must not contain a NUL byte.", operation="configure"
        )
    try:
        path = os.path.abspath(os.path.expanduser(path))
    except (OSError, ValueError):
        raise InvalidArgumentError(
            f"{name} is not a usable filesystem path.", operation="configure"
        ) from None
    if path == os.path.dirname(path):
        raise InvalidArgumentError(
            f"{name} must not be a filesystem root.", operation="configure"
        )
    try:
        os.fsencode(path)
    except UnicodeError:
        raise InvalidArgumentError(
            f"{name} cannot be represented as a filesystem path.",
            operation="configure",
        ) from None
    return path


def _executable(value) -> Optional[str]:
    """Normalize an optional proot executable path without probing it."""
    if value is None:
        return None
    try:
        path = os.fspath(value)
    except TypeError:
        raise InvalidArgumentError(
            "proot_path must be a string, path-like object, or None.",
            operation="configure",
        ) from None
    if not isinstance(path, str) or not path:
        raise InvalidArgumentError(
            "proot_path must be a non-empty text path or None.",
            operation="configure",
        )
    if "\0" in path:
        raise InvalidArgumentError(
            "proot_path must not contain a NUL byte.", operation="configure"
        )
    try:
        path = os.path.abspath(os.path.expanduser(path))
    except (OSError, ValueError):
        raise InvalidArgumentError(
            "proot_path is not a usable filesystem path.",
            operation="configure",
        ) from None
    try:
        os.fsencode(path)
    except UnicodeError:
        raise InvalidArgumentError(
            "proot_path cannot be represented as a filesystem path.",
            operation="configure",
        ) from None
    return path


@dataclass(frozen=True)
class RuntimeConfig:
    """Storage and executable settings for a :class:`ProotDistro` instance.

    Paths are normalized when the configuration is created. They identify
    trust roots: API operations may create, modify, and remove state below
    them. The two roots must therefore be distinct and must not be a
    filesystem root.
    """

    runtime_dir: _PathArgument
    cache_dir: _PathArgument
    proot_path: Optional[_PathArgument] = None

    def __post_init__(self) -> None:
        runtime_dir = _directory(self.runtime_dir, "runtime_dir")
        cache_dir = _directory(self.cache_dir, "cache_dir")
        proot_path = _executable(self.proot_path)
        if runtime_dir == cache_dir:
            raise InvalidArgumentError(
                "runtime_dir and cache_dir must be different directories.",
                operation="configure",
            )
        try:
            runtime_under_cache = (
                os.path.commonpath((runtime_dir, cache_dir)) == cache_dir
            )
        except ValueError:
            runtime_under_cache = False
        if runtime_under_cache:
            raise InvalidArgumentError(
                "runtime_dir must not be inside cache_dir.",
                operation="configure",
            )
        object.__setattr__(self, "runtime_dir", runtime_dir)
        object.__setattr__(self, "cache_dir", cache_dir)
        object.__setattr__(self, "proot_path", proot_path)

    @classmethod
    def default(cls) -> "RuntimeConfig":
        """Return the configuration used by the command-line utility."""
        from proot_distro.constants import BASE_CACHE_DIR, RUNTIME_DIR

        return cls(
            runtime_dir=RUNTIME_DIR,
            cache_dir=BASE_CACHE_DIR,
            proot_path=os.environ.get("PD_PROOT_BIN") or None,
        )


__all__ = ("RuntimeConfig",)
