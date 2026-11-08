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

"""Immutable internal results returned by shared operation services."""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class CacheCleanup:
    """Cache cleanup mode and measured byte counts."""

    mode: str
    bytes_before: int
    bytes_after: int


@dataclass(frozen=True)
class ContainerRemoval:
    """Description of a removed container-state entry."""

    name: str
    was_directory: bool


@dataclass(frozen=True)
class ContainerRename:
    """Description of a completed container rename."""

    old_name: str
    new_name: str


@dataclass(frozen=True)
class InstalledContainer:
    """An installed container and its recorded image origin."""

    name: str
    image_reference: Optional[str]
    architecture: Optional[str]
    image_id: Optional[str]


@dataclass(frozen=True)
class CachedImage:
    """Validated cached-image metadata used by operation services."""

    identifier: str
    reference: str
    repository: str
    architecture: str
    image_id: str
    size_bytes: int
    layer_count: int
    missing_layers: int
    created: str
    cached_at: float
    cache_key: str
    cache_image_id: str


@dataclass(frozen=True)
class ImageRemoval:
    """Images removed together with cleanup accounting and dependents."""

    removed: tuple
    reclaimed_bytes: int
    dependent_containers: tuple


@dataclass(frozen=True)
class ActiveSession:
    """One validated live session registry record."""

    pid: int
    container: str
    kind: str
    command: Tuple[str, ...]
    user: str
    start_time: float
    isolated: bool
    minimal: bool
    detached: bool


@dataclass(frozen=True)
class Transfer:
    """A completed or partially completed filesystem transfer."""

    operation: str
    source: str
    destination: str
    failures: int = 0


__all__ = (
    "ActiveSession",
    "CacheCleanup",
    "CachedImage",
    "ContainerRemoval",
    "ContainerRename",
    "ImageRemoval",
    "InstalledContainer",
    "Transfer",
)
