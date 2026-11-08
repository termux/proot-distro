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

"""Shared cached-image removal operation."""

import os
from contextlib import ExitStack

from proot_distro import dirfd
from proot_distro.helpers.docker import (
    ARCH_TO_DOCKER,
    canonical_ref,
    image_cache_entry,
    iter_cached_images,
    layer_cache_name,
    layer_cache_path,
    open_layer_cache_dir,
    open_manifest_cache_dir,
    parse_image_ref,
    with_explicit_tag,
)
from proot_distro.locking import BuildLock
from proot_distro.message import quote_error, quote_path
from proot_distro.operation_errors import OperationFailure
from proot_distro.operations.context import OperationContext
from proot_distro.operations.errors import (
    AmbiguousImageFailure,
    ImageNotFoundFailure,
)
from proot_distro.operations.locks import operation_lock
from proot_distro.operations.models import CachedImage, ImageRemoval
from proot_distro.paths import container_image_origin, installed_container_names
from proot_distro.progress import fmt_size


_MIN_ID_PREFIX = 4


def _context(value):
    return value if value is not None else OperationContext()


def _text(value):
    return value if isinstance(value, str) else ""


def _record(record) -> CachedImage:
    reference = _text(record.get("image_ref"))
    cache_image_id = _text(record.get("image_id"))
    return CachedImage(
        identifier=(reference or cache_image_id[:12] or record["key"]),
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


def _display(record) -> str:
    if record["image_ref"]:
        return quote_path(record["image_ref"])
    if record["repo"]:
        return f"{quote_path(record['repo'])}:<none>"
    return quote_path(record["key"])


def _resource(record) -> str:
    return record["image_ref"] or record["image_id"][:12] or record["key"]


def _arch(record) -> str:
    return quote_path(record["arch"]) if record["arch"] else "unknown arch"


def remove_image(
    identifier: str,
    *,
    architecture=None,
    context: OperationContext = None,
) -> ImageRemoval:
    """Remove cached image variants and blobs no surviving image references."""
    context = _context(context)
    targets = _resolve_images(identifier, architecture)
    if not targets:
        similar = tuple(
            _record(record) for record in iter_cached_images()
            if _same_name(record, identifier)
        )
        raise ImageNotFoundFailure(identifier, architecture, similar)

    removed = tuple(_record(record) for record in targets)
    with ExitStack() as locks:
        for lock in _image_locks(identifier, targets):
            locks.enter_context(operation_lock(lock))
        reclaimed, dependents = _delete_images(targets, context)
    return ImageRemoval(removed, reclaimed, tuple(dependents))


def _resolve_images(identifier: str, architecture) -> list:
    archs = [architecture] if architecture else list(ARCH_TO_DOCKER)
    reference = with_explicit_tag(identifier)
    matches, seen = [], set()
    for candidate in archs:
        record = image_cache_entry(reference, candidate)
        if record is not None and record["path"] not in seen:
            seen.add(record["path"])
            matches.append(record)
    if matches:
        return matches

    wanted = identifier.lower()
    for record in iter_cached_images():
        if architecture and record["arch"] and record["arch"] != architecture:
            continue
        if record["key"] == wanted or (
            len(wanted) >= _MIN_ID_PREFIX
            and record["image_id"].lower().startswith(wanted)
        ):
            matches.append(record)

    if len({record["image_id"] for record in matches}) > 1:
        raise AmbiguousImageFailure(
            identifier, tuple(_record(record) for record in matches)
        )
    return matches


def _image_locks(identifier: str, targets: list) -> list:
    locks = {}
    for record in targets:
        if not record["arch"]:
            continue
        for reference in (
            record["image_ref"], with_explicit_tag(identifier),
        ):
            if not reference:
                continue
            lock = BuildLock(reference, record["arch"], command="remove")
            locks.setdefault(lock.lock_path, lock)
    return [locks[path] for path in sorted(locks)]


def _delete_images(targets, context):
    for record in targets:
        context.info(
            f"Removing image '{_display(record)}' ({_arch(record)})...",
            phase="remove-image", resource=_resource(record),
        )

    try:
        manifest_fd = open_manifest_cache_dir()
    except OSError as exc:
        raise OperationFailure(
            f"cannot read the manifest cache: {quote_error(exc)}"
        ) from exc
    try:
        try:
            layer_fd = open_layer_cache_dir()
        except FileNotFoundError:
            layer_fd = None
        except OSError as exc:
            raise OperationFailure(
                f"cannot read the layer cache: {quote_error(exc)}"
            ) from exc
        try:
            failed, reclaimed = _unlink_targets(
                manifest_fd, layer_fd, targets, context
            )
        finally:
            if layer_fd is not None:
                os.close(layer_fd)
    finally:
        os.close(manifest_fd)

    dependents = _containers_from_images(targets)
    if dependents:
        names = ", ".join(f"'{name}'" for name in dependents)
        context.info(
            f"Container(s) {names} were installed from this image and "
            f"keep working.",
            phase="remove-image", resource=_resource(targets[0]),
        )
    if failed:
        raise OperationFailure(
            "Finished with errors. Some files probably were not deleted."
        )
    context.info(
        f"Reclaimed {fmt_size(reclaimed)} of disk space.",
        phase="remove-image", resource=_resource(targets[0]),
    )
    return reclaimed, dependents


def _unlink_targets(manifest_fd, layer_fd, targets, context):
    failed = False
    reclaimed = 0
    for record in targets:
        path = record["path"]
        size = _entry_size(manifest_fd, record["name"])
        try:
            os.unlink(record["name"], dir_fd=manifest_fd)
        except OSError as exc:
            context.error(
                f"Cannot remove '{quote_path(path)}': {quote_error(exc)}",
                phase="remove-image", resource=_resource(record),
            )
            failed = True
            continue
        reclaimed += size
        if context.verbose:
            context.info(
                f"Removed: '{quote_path(path)}'",
                phase="remove-image", resource=_resource(record),
            )

    keep = set()
    for record in iter_cached_images():
        keep.update(layer["digest"] for layer in record["layers"])

    dropped = set()
    for record in targets:
        for layer in record["layers"]:
            digest = layer["digest"]
            if digest in keep or digest in dropped:
                continue
            dropped.add(digest)
            try:
                name = layer_cache_name(digest)
            except RuntimeError:
                continue
            if layer_fd is None:
                continue
            path = layer_cache_path(digest)
            size = _entry_size(layer_fd, name)
            try:
                os.unlink(name, dir_fd=layer_fd)
            except FileNotFoundError:
                continue
            except OSError as exc:
                context.error(
                    f"Cannot remove '{quote_path(path)}': {quote_error(exc)}",
                    phase="remove-image", resource=digest,
                )
                failed = True
                continue
            reclaimed += size
            if context.verbose:
                context.info(
                    f"Removed: '{quote_path(path)}'",
                    phase="remove-image", resource=digest,
                )
    return failed, reclaimed


def _containers_from_images(targets):
    wanted = {
        (canonical_ref(record["image_ref"]), record["arch"])
        for record in targets if record["image_ref"]
    }
    if not wanted:
        return []
    found = []
    for name in installed_container_names():
        reference, architecture = container_image_origin(name)
        if reference and (canonical_ref(reference), architecture) in wanted:
            found.append(name)
    return found


def _same_name(record, identifier):
    reference = record["image_ref"] or record["repo"]
    if not reference:
        return False
    repository = parse_image_ref(reference)[1]
    wanted = parse_image_ref(identifier)[1]
    return (
        repository == wanted
        or repository.split("/")[-1] == wanted.split("/")[-1]
    )


def _entry_size(dir_fd, name):
    try:
        return dirfd.lstat_at(dir_fd, name).st_size
    except OSError:
        return 0


__all__ = ("remove_image",)
