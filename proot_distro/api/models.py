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

"""Immutable result models returned by the public API."""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ContainerInfo:
    """An installed container and its recorded image origin, if any."""

    name: str
    image_reference: Optional[str]
    architecture: Optional[str]
    image_id: Optional[str]


@dataclass(frozen=True)
class ImageInfo:
    """An OCI image represented by the local manifest and layer caches."""

    identifier: str
    reference: Optional[str]
    repository: Optional[str]
    architecture: Optional[str]
    image_id: Optional[str]
    size_bytes: int
    layer_count: int
    missing_layers: int
    created: Optional[str]
    cached_at: float

    @property
    def complete(self) -> bool:
        """Whether every layer referenced by the image is cached."""
        return self.missing_layers == 0


@dataclass(frozen=True)
class SessionInfo:
    """A live login or image-entrypoint session."""

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
class SearchHit:
    """One public Docker Hub repository returned by a search."""

    name: str
    description: str
    stars: int
    pulls: int
    official: bool
    automated: bool


@dataclass(frozen=True)
class SearchResult:
    """Docker Hub search hits and Hub's total matching count."""

    query: str
    total: int
    hits: Tuple[SearchHit, ...]


@dataclass(frozen=True)
class TransferResult:
    """A completed copy, move, or synchronization operation."""

    operation: str
    source: str
    destination: str


@dataclass(frozen=True)
class BackupResult:
    """A container backup written to a host file."""

    container: str
    output: str
    compression: str
    size_bytes: int


@dataclass(frozen=True)
class RestoreResult:
    """A container restored from a host archive."""

    container: ContainerInfo
    archive: str


__all__ = (
    "BackupResult",
    "ContainerInfo",
    "ImageInfo",
    "SearchHit",
    "SearchResult",
    "SessionInfo",
    "RestoreResult",
    "TransferResult",
)
