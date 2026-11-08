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

"""Shared immutable inventories for CLI and programmatic adapters."""

from proot_distro.helpers.docker.cache import iter_cached_images
from proot_distro.operations.models import (
    ActiveSession,
    CachedImage,
    InstalledContainer,
)
from proot_distro.paths import (
    installed_container_names,
    read_container_manifest,
)
from proot_distro.session import active_sessions


def _text(value):
    """Return non-empty text, otherwise an empty internal value."""
    return value if isinstance(value, str) and value else ""


def _container(name: str) -> InstalledContainer:
    """Build one container record without failing the whole inventory."""
    try:
        data = read_container_manifest(name)
    except (OSError, ValueError):
        data = {}

    image_reference = _text(data.get("image_ref")) or None
    architecture = _text(data.get("arch")) or None
    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        manifest = {}
    config = manifest.get("config")
    if not isinstance(config, dict):
        config = {}
    image_id = _text(config.get("digest")) or None

    return InstalledContainer(
        name=name,
        image_reference=image_reference,
        architecture=architecture,
        image_id=image_id,
    )


def cached_image_from_record(record: dict) -> CachedImage:
    """Convert one validated cache helper record to an immutable model."""
    reference = _text(record.get("image_ref"))
    cache_image_id = _text(record.get("image_id"))
    return CachedImage(
        identifier=reference or cache_image_id[:12] or record["key"],
        reference=reference,
        repository=_text(record.get("repo")),
        architecture=_text(record.get("arch")),
        image_id=_text(record.get("config_digest")),
        size_bytes=record["size"],
        layer_count=len(record["layers"]),
        missing_layers=record["missing"],
        created=_text(record.get("created")),
        cached_at=float(record["cached_at"]),
        cache_key=record["key"],
        cache_image_id=cache_image_id,
    )


def list_containers() -> tuple:
    """Return installed containers sorted by name."""
    return tuple(_container(name) for name in installed_container_names())


def list_images() -> tuple:
    """Return cached OCI images in deterministic cache order."""
    return tuple(cached_image_from_record(item) for item in iter_cached_images())


def list_sessions() -> tuple:
    """Return validated live sessions sorted by start time and PID."""
    return tuple(
        ActiveSession(
            pid=record["pid"],
            container=record["container"],
            kind=record["kind"],
            command=tuple(record["command"]),
            user=record["user"],
            start_time=record["start_time"],
            isolated=record["isolated"],
            minimal=record["minimal"],
            detached=record["detach"],
        )
        for record in active_sessions()
    )


__all__ = (
    "cached_image_from_record",
    "list_containers",
    "list_images",
    "list_sessions",
)
