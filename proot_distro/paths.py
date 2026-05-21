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

# Architecture: Path helpers for the container storage layout and the
# `[container:]path` spec accepted by `copy` / `sync`.
#
#   container_dir / container_rootfs / container_manifest
#       Compose the per-container paths under CONTAINERS_DIR so no
#       caller has to spell out the layout (containers/<name>/...).
#
#   container_from_spec / resolve_container_path
#       Decode the `[container:]path` spec format.
#
#   container_locks_for_spec_pair
#       Build the ContainerLock list a copy/sync invocation needs:
#       shared on the source, exclusive on the destination, with
#       same-container same-lock dedup and deterministic ordering.

import os
import sys

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.message import crit_error
from proot_distro.locking import ContainerLock
from proot_distro.names import is_valid_name


def container_dir(name: str) -> str:
    """Return the absolute path to a container's top-level directory."""
    return os.path.join(CONTAINERS_DIR, name)


def container_rootfs(name: str) -> str:
    """Return the absolute path to a container's rootfs directory."""
    return os.path.join(container_dir(name), "rootfs")


def container_manifest(name: str) -> str:
    """Return the absolute path to a container's manifest.json sentinel."""
    return os.path.join(container_dir(name), "manifest.json")


def container_from_spec(spec: str):
    """Return the container name in a `name:path` spec, or None."""
    return spec.split(":", 1)[0] if ":" in spec else None


def resolve_container_path(spec: str) -> str:
    """Resolve a `name:path` or plain host path to an absolute host path.

    For a `name:path` spec the result is forced to stay inside the
    container's rootfs — an attempt to traverse out with `..` segments
    is rejected with a fatal error. An empty name (`:path`) is also
    rejected: without the check rootfs would degenerate to CONTAINERS_DIR
    itself and the spec would silently scribble into a stranger area
    of the runtime tree. For a plain path the spec is just expanded
    to its absolute form.
    """
    if ":" not in spec:
        return os.path.normpath(os.path.abspath(spec))

    name, _, rel_path = spec.partition(":")
    if not is_valid_name(name):
        crit_error(f"invalid container name '{name}' in spec '{spec}'.")
        sys.exit(1)
    rootfs = os.path.normpath(container_rootfs(name))
    if not os.path.isdir(rootfs):
        crit_error(f"container '{name}' does not exist.")
        sys.exit(1)
    resolved = os.path.normpath(os.path.join(rootfs, rel_path.lstrip("/")))
    if resolved != rootfs and not resolved.startswith(rootfs + os.sep):
        crit_error("destination path escapes the container directory.")
        sys.exit(1)
    return resolved


def container_locks_for_spec_pair(src_spec: str, dst_spec: str, command: str):
    """Return ContainerLock instances needed for a `src -> dst` op.

    Used by `copy` and `sync`. The destination side always needs an
    exclusive lock; the source side needs a shared lock. When both
    specs name the same container, a single exclusive lock suffices.
    The list is returned in sorted-name order so concurrent invocations
    acquire locks in a consistent sequence (no theoretical deadlock).
    """
    src_name = container_from_spec(src_spec)
    dst_name = container_from_spec(dst_spec)
    if src_name and dst_name:
        if src_name == dst_name:
            return [ContainerLock(src_name, exclusive=True, command=command)]
        return [
            ContainerLock(name, exclusive=(name == dst_name), command=command)
            for name in sorted({src_name, dst_name})
        ]
    if dst_name:
        return [ContainerLock(dst_name, exclusive=True, command=command)]
    if src_name:
        return [ContainerLock(src_name, exclusive=False, command=command)]
    return []
