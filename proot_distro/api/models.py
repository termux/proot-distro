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

    def __post_init__(self) -> None:
        object.__setattr__(self, "command", tuple(self.command))


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

    def __post_init__(self) -> None:
        object.__setattr__(self, "hits", tuple(self.hits))


@dataclass(frozen=True)
class TransferResult:
    """A completed copy, move, or synchronization operation."""

    operation: str
    source: str
    destination: str


@dataclass(frozen=True)
class BackupResult:
    """A container backup written to a host path or caller-owned stream."""

    container: str
    output: Optional[str]
    compression: str
    size_bytes: int


@dataclass(frozen=True)
class RestoreResult:
    """A container restored from a host path or caller-owned stream."""

    container: ContainerInfo
    archive: Optional[str]


@dataclass(frozen=True)
class BuildResult:
    """Images and optional artifacts produced by a Dockerfile build."""

    images: Tuple[ImageInfo, ...]
    outputs: Tuple[Optional[str], ...]
    installed_container: Optional[ContainerInfo]

    def __post_init__(self) -> None:
        object.__setattr__(self, "images", tuple(self.images))
        object.__setattr__(self, "outputs", tuple(self.outputs))


@dataclass(frozen=True)
class PushResult:
    """The registry result of publishing one image tag."""

    registry: str
    repository: str
    tag: str
    manifest_digest: Optional[str]
    bytes_uploaded: int


@dataclass(frozen=True)
class ImageRemovalResult:
    """Cached image variants removed by one operation."""

    removed: Tuple[ImageInfo, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "removed", tuple(self.removed))


@dataclass(frozen=True)
class CacheCleanupResult:
    """Disk usage before and after a cache cleanup."""

    mode: str
    bytes_before: int
    bytes_after: int
    bytes_reclaimed: int


@dataclass(frozen=True)
class SessionSignalResult:
    """Tracked session roots selected for one signal operation."""

    target: str
    signal: str
    pids: Tuple[int, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "pids", tuple(self.pids))


@dataclass(frozen=True)
class DetachedSession:
    """A detached login or image-entrypoint session started by the API."""

    pid: int
    container: str
    kind: str


@dataclass(frozen=True)
class SessionCommand:
    """Inspectable path-based launch description for a container session.

    ``argv`` names ``rootfs`` by path for display and external inspection.
    Actual API sessions use a validated directory descriptor and
    ``--rootfs=.`` instead; executing this description cannot reproduce that
    descriptor pinning guarantee.
    """

    kind: str
    container: str
    executable: str
    argv: Tuple[str, ...]
    environment: Tuple[Tuple[str, str], ...]
    inner_command: Tuple[str, ...]
    rootfs: str
    work_dir: str
    user: str
    shell_command: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "argv", tuple(self.argv))
        object.__setattr__(
            self, "environment",
            tuple((key, value) for key, value in self.environment),
        )
        object.__setattr__(self, "inner_command", tuple(self.inner_command))

    @property
    def environment_dict(self) -> dict:
        """Return a mutable copy of the inspected environment."""
        return dict(self.environment)


__all__ = (
    "BackupResult",
    "BuildResult",
    "CacheCleanupResult",
    "ContainerInfo",
    "DetachedSession",
    "ImageInfo",
    "ImageRemovalResult",
    "PushResult",
    "SearchHit",
    "SearchResult",
    "SessionInfo",
    "SessionCommand",
    "SessionSignalResult",
    "RestoreResult",
    "TransferResult",
)
