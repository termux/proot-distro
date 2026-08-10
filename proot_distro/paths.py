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
#       Decode the `[container:]path` spec format. The container side is
#       resolved with chroot semantics (see _resolve_within_root) so a
#       symlink planted in the rootfs cannot redirect the operation onto
#       the host filesystem.
#
#   pin_path
#       Re-walk an already-resolved container path with O_NOFOLLOW and
#       keep the directory fd open, so the I/O that follows cannot be
#       redirected by a symlink planted after the resolve (TOCTOU).
#
#   container_locks_for_spec_pair
#       Build the ContainerLock list a copy/sync invocation needs:
#       shared on the source, exclusive on the destination, with
#       same-container same-lock dedup and deterministic ordering.

import os
import sys
from contextlib import contextmanager

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.message import crit_error, warn
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


# Upper bound on symlink hops taken while resolving one spec, mirroring
# the kernel's ELOOP limit. Guards against link cycles (a -> b -> a).
_MAX_SYMLINK_HOPS = 40


def _resolve_within_root(root: str, rel_path: str, spec: str) -> str:
    """Resolve *rel_path* under *root* the way the guest would see it.

    Path components are consumed one at a time and every symlink met on
    the way is expanded with *root* standing in for `/`: an absolute link
    target restarts the walk at *root*, a relative one continues from the
    directory holding the link, and `..` is clamped so it can never climb
    above *root*. The returned path is therefore always inside *root* and
    contains no symlink components.

    Purely lexical normalisation is not enough here. `os.path.normpath`
    collapses `..` without looking at the filesystem, so a symlink planted
    inside the rootfs — `escape -> /`, which is perfectly ordinary as seen
    from inside the container — would pass a `startswith(rootfs)` check
    and then be followed by the copy, reading from or writing to the host
    filesystem outside the container.
    """
    resolved = root
    pending = rel_path.split("/")
    hops = 0

    while pending:
        part = pending.pop(0)
        if part in ("", "."):
            continue
        if part == "..":
            # Clamped: at the root, `..` is the root (same as chroot).
            if resolved != root:
                resolved = os.path.dirname(resolved)
            continue

        candidate = os.path.join(resolved, part)
        try:
            target = os.readlink(candidate)
        except OSError:
            # Not a symlink, or does not exist yet (the destination of a
            # copy usually does not) — take the component literally.
            resolved = candidate
            continue

        hops += 1
        if hops > _MAX_SYMLINK_HOPS:
            crit_error(f"too many symbolic links while resolving '{spec}'.")
            sys.exit(1)
        if target.startswith("/"):
            resolved = root
        # Re-queue the link target so its own components (including any
        # further symlinks) go through exactly the same treatment.
        pending = target.split("/") + pending

    return resolved


def resolve_container_path(spec: str) -> str:
    """Resolve a `name:path` or plain host path to an absolute host path.

    For a `name:path` spec the result is forced to stay inside the
    container's rootfs. An attempt to traverse out with `..` segments
    written in the spec itself is rejected with a fatal error; symlinks
    stored in the rootfs are instead resolved against the rootfs as if it
    were `/`, matching what the container sees and denying any escape
    (see _resolve_within_root). An empty name (`:path`) is also
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
    rel_path = rel_path.lstrip("/")
    # A `..` typed into the spec is a user mistake, not container content:
    # report it instead of silently clamping it to the rootfs.
    lexical = os.path.normpath(os.path.join(rootfs, rel_path))
    if lexical != rootfs and not lexical.startswith(rootfs + os.sep):
        crit_error("destination path escapes the container directory.")
        sys.exit(1)
    return _resolve_within_root(rootfs, rel_path, spec)


# O_PATH opens a directory without needing read permission on it, which
# matters for the execute-only directories `sync` deliberately tolerates.
# It is Linux-only; fall back to a plain directory open elsewhere.
_O_DIR = (getattr(os, "O_PATH", 0) or os.O_RDONLY) | os.O_DIRECTORY


def _proc_fd_usable() -> bool:
    """True when /proc/self/fd/<n>/<name> can be used for path I/O."""
    try:
        return os.path.isdir("/proc/self/fd")
    except OSError:
        return False


class PinnedPath:
    """A resolved path plus the directory fd that pins it in place.

    `str(pin)` is the real path, for messages. `pin.io` is the path to
    hand to the filesystem: `/proc/self/fd/<n>[/<leaf>]` when pinning is
    available, which keeps referring to the directory that was validated
    even if its *name* is later swapped for a symlink. `pin.dir_fd` and
    `pin.leaf` are for callers that want to open the final component
    themselves with O_NOFOLLOW (see copy's single-file path).
    """

    def __init__(self, path: str, dir_fd=None, leaf: str = "") -> None:
        self.path = path
        self.dir_fd = dir_fd
        self.leaf = leaf

    @property
    def io(self) -> str:
        if self.dir_fd is None:
            return self.path
        base = f"/proc/self/fd/{self.dir_fd}"
        if self.leaf:
            return os.path.join(base, self.leaf)
        # /proc/self/fd/<n> is itself a symlink, so lstat() on it reports
        # a link rather than the directory. A trailing separator forces
        # resolution through it, which is what every caller means when
        # the pin covers the directory itself.
        return base + os.sep

    def __str__(self) -> str:
        return self.path

    def __fspath__(self) -> str:
        return self.io


@contextmanager
def pin_path(spec: str, resolved: str, *, inside: bool = False):
    """Yield a PinnedPath for *resolved*, the result of resolving *spec*.

    resolve_container_path() returns a path with no symlink components,
    but resolving and then using it are two steps: a process inside the
    container can swap a directory for a symlink in between, and the
    copy would follow it out to the host. Re-walking the components with
    O_NOFOLLOW closes that window twice over — it *detects* the swap (a
    component that is now a symlink fails with ELOOP, and the command
    aborts) and it *pins* what it validated, since the returned fd keeps
    naming the same directory inode no matter what happens to the name.

    By default the *parent* is pinned and the final component is carried
    as `leaf`, which is what a caller operating on the path itself needs
    (copy, move, and any O_NOFOLLOW open of the leaf). Pass inside=True
    for a path the caller only ever writes *underneath* — sync's source
    and destination roots — to walk the final component too and pin that
    directory itself. inside=True therefore also *refuses* a root that
    has become a symlink, which the default cannot do: writes would go
    straight through it.

    Host paths (specs with no container prefix) are outside the threat
    model and are yielded unpinned, as is everything when /proc is not
    available — the pinned form is built from /proc/self/fd.
    """
    name = container_from_spec(spec)
    if name is None or not _proc_fd_usable():
        yield PinnedPath(resolved)
        return

    rootfs = os.path.normpath(container_rootfs(name))
    rel = os.path.relpath(resolved, rootfs)
    parts = [] if rel == os.curdir else rel.split(os.sep)
    leaf = "" if inside else (parts.pop() if parts else "")

    fd = None
    try:
        try:
            fd = os.open(rootfs, _O_DIR)
            for part in parts:
                nxt = os.open(part, _O_DIR | os.O_NOFOLLOW, dir_fd=fd)
                os.close(fd)
                fd = nxt
        except OSError as exc:
            if fd is not None:
                os.close(fd)
                fd = None
            # ELOOP means a component became a symlink between the
            # resolve and now — exactly the race this guards against.
            crit_error(
                f"path '{spec}' changed while it was being resolved "
                f"({exc.strerror}); refusing to continue."
            )
            sys.exit(1)
        yield PinnedPath(resolved, fd, leaf)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def open_pinned_leaf(pin: PinnedPath, flags: int, mode: int = 0o644) -> int:
    """Open the path *pin* designates, refusing a symlink at the leaf.

    The pinned fd protects every component above the final one. The
    final component still has to be opened by name, so O_NOFOLLOW is the
    only thing standing between a symlink planted there and a write to
    whatever it points at. Unpinned paths (host side, or no /proc) are
    opened directly, still with O_NOFOLLOW.
    """
    if pin.dir_fd is None or not pin.leaf:
        return os.open(pin.path, flags | os.O_NOFOLLOW, mode)
    return os.open(pin.leaf, flags | os.O_NOFOLLOW, mode, dir_fd=pin.dir_fd)


def warn_unpinned(spec: str) -> None:
    """Warn once that a container path could not be pinned."""
    if container_from_spec(spec) and not _proc_fd_usable():
        warn(f"/proc is not available: cannot protect '{spec}' against a "
             f"symlink swapped in by a concurrent container process.")


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
