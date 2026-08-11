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
#   refuse_src_dest_overlap
#       Reject a destination that is the source, or sits inside it, once
#       both have been resolved — the point at which a planted symlink can
#       no longer hide the overlap.
#
#   pin_path
#       Re-walk an already-resolved container path with O_NOFOLLOW and
#       keep the directory fd open, so the I/O that follows cannot be
#       redirected by a symlink planted after the resolve (TOCTOU).
#       With create=True the same walk also makes the missing parents,
#       so no caller has to create them by path beforehand.
#
#   resolve_container_child
#       Re-resolve a destination that has been extended with the source's
#       base name, so the appended component gets the same chroot walk as
#       one that was written in the spec.
#
#   container_locks_for_spec_pair
#       Build the ContainerLock list a copy/sync invocation needs:
#       shared on the source, exclusive on the destination, with
#       same-container same-lock dedup and deterministic ordering.

import os
import stat
import sys
from contextlib import contextmanager

from proot_distro import dirfd
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


def resolve_container_path(spec: str, *, deref_leaf: bool = True) -> str:
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

    Pass deref_leaf=False for an operation that acts on the last component
    *itself* rather than on what it names — `copy --move`, which renames the
    entry, as mv does. Only the parents then get the chroot walk. Without
    it, moving a container symlink would resolve to the link's target and
    move that instead, leaving the link behind and dangling; a host source
    gets the same treatment by not being passed through realpath().
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
    if not deref_leaf:
        head, _, tail = rel_path.rstrip("/").rpartition("/")
        # `.` and `..` name no entry of their own, so there is nothing to
        # keep: let the full walk collapse them as it always does.
        if tail and tail not in (os.curdir, os.pardir):
            return os.path.join(_resolve_within_root(rootfs, head, spec), tail)
    return _resolve_within_root(rootfs, rel_path, spec)


def refuse_src_dest_overlap(src_spec: str, src_path: str,
                            dest_spec: str, dest_path: str) -> None:
    """Exit when the destination is the source, or lies inside it.

    Both paths are already resolved, so this weighs what the transfer will
    really touch rather than what was typed. That matters: a symlink the
    guest planted in the rootfs (`backup -> /data`) is enough to make
    `copy -r box:/data box:/backup` a directory copied into itself, which
    recursed until the interpreter's stack gave out and left a partial tree
    behind. Source onto itself is refused for the reason cp refuses it too —
    the destination is opened with O_TRUNC while the source is still being
    read, so the content is lost.
    """
    same = src_path == dest_path
    if not same:
        try:
            same = os.path.samestat(os.lstat(src_path), os.lstat(dest_path))
        except OSError:
            same = False            # one of them does not exist yet: fine
    if same:
        crit_error(f"'{src_spec}' and '{dest_spec}' are the same file.")
        sys.exit(1)
    if dest_path.startswith(src_path.rstrip(os.sep) + os.sep):
        crit_error(f"cannot copy '{src_spec}' into itself: "
                   f"'{dest_spec}' is inside it.")
        sys.exit(1)


def resolve_container_child(spec: str, resolved: str, child: str) -> str:
    """Resolve *child* under the already-resolved container path *resolved*.

    `copy` and `sync` extend a destination directory with the source's
    base name, so that `copy f box:/dir` writes to `box:/dir/f`. That
    appended component is container content like any other and has to go
    through the same chroot walk as one written in the spec: `/dir/f` may
    itself be a symlink, and joining it literally would leave an
    unresolved link at the leaf, which the O_NOFOLLOW open then refuses —
    failing an operation that succeeds when spelled `box:/dir/f`.

    A host destination is joined as-is; host paths are not walked.
    """
    joined = os.path.join(resolved, child)
    name = container_from_spec(spec)
    if name is None:
        return joined
    rootfs = os.path.normpath(container_rootfs(name))
    return _resolve_within_root(rootfs, os.path.relpath(joined, rootfs), spec)


# O_PATH opens a directory without needing read permission on it, which
# matters for the execute-only directories `sync` deliberately tolerates.
# dirfd.reopen() turns such a pin into a readable fd when one is needed.
# O_PATH is Linux-only; fall back to a plain directory open elsewhere.
_O_DIR = (getattr(os, "O_PATH", 0) or os.O_RDONLY) | os.O_DIRECTORY


class PinnedPath:
    """A resolved path together with an fd pinning the directory it is in.

    `str(pin)` is the real path, for messages. `pin.dir_fd` and
    `pin.leaf` are what every filesystem call should use: the fd refers
    to a directory *inode* that an O_NOFOLLOW walk has just validated, so
    renaming a directory cannot re-point it, and `leaf` is the single
    remaining name, which callers open with O_NOFOLLOW themselves (see
    dirfd.open_file_at). A pin taken with inside=True has an empty leaf
    and its fd refers to the path itself.
    """

    __slots__ = ("path", "dir_fd", "leaf")

    def __init__(self, path: str, dir_fd: int, leaf: str = "") -> None:
        self.path = path
        self.dir_fd = dir_fd
        self.leaf = leaf

    def __str__(self) -> str:
        return self.path


class _Refused(OSError):
    """A component of the walk is a symlink now, and was not at resolve time."""


def _is_link_at(fd: int, part: str) -> bool:
    """True when *part* under *fd* is a symlink right now."""
    try:
        return stat.S_ISLNK(dirfd.lstat_at(fd, part).st_mode)
    except OSError:
        return False


def _descend(fd: int, part: str, create: bool) -> int:
    """Open *part* under *fd*, close *fd*, and return the new fd.

    With create=True a missing component is made on the way down. The
    mkdir is relative to a directory fd the walk has already validated
    and the open that follows is O_NOFOLLOW, so a component created here
    is no more redirectable than one that was already present — which is
    the whole reason the parents are not made by path beforehand.

    A refusal is raised as _Refused only once the component has been
    confirmed to be a symlink. ENOTDIR covers two unrelated things — the
    O_NOFOLLOW open declining a link, and a component that is simply not a
    directory (`copy x box:/etc/passwd/y`, a plain mistake) — and reporting
    the second as a race would send the user hunting for an attack.
    """
    try:
        nxt = os.open(part, _O_DIR | os.O_NOFOLLOW, dir_fd=fd)
    except FileNotFoundError:
        if not create:
            raise
        try:
            # 0o777 & ~umask, the mode os.makedirs() used to apply here.
            os.mkdir(part, 0o777, dir_fd=fd)
        except FileExistsError:
            pass                # lost a race with another writer; open it
        nxt = os.open(part, _O_DIR | os.O_NOFOLLOW, dir_fd=fd)
    except OSError as exc:
        if dirfd.is_refusal(exc) and _is_link_at(fd, part):
            raise _Refused(exc.errno, exc.strerror, part) from None
        raise
    os.close(fd)
    return nxt


@contextmanager
def pin_path(spec: str, resolved: str, *, inside: bool = False,
             create: bool = False):
    """Yield a PinnedPath for *resolved*, the result of resolving *spec*.

    resolve_container_path() returns a path with no symlink components,
    but resolving and then using it are two steps: a process inside the
    container can swap a directory for a symlink in between, and the
    copy would follow it out to the host. Re-walking the components with
    O_NOFOLLOW closes that window twice over — it *detects* the swap (a
    component that is now a symlink fails, and the command aborts) and it
    *pins* what it validated, since the returned fd keeps naming the same
    directory inode no matter what happens to the name.

    By default the *parent* is pinned and the final component is carried
    as `leaf`, which is what a caller operating on the path itself needs
    (copy, move, and any O_NOFOLLOW open of the leaf). Pass inside=True
    for a path the caller only ever works *underneath* — sync's source
    and destination roots — to walk the final component too and pin that
    directory itself. inside=True therefore also *refuses* a root that
    has become a symlink, which the default cannot do: everything written
    below it would go straight through.

    Pass create=True when the caller needs the walked directories to
    exist. Making them here rather than with os.makedirs() beforehand is
    what keeps the guarantee whole: makedirs() addresses each level by
    path, so a component swapped for a symlink between the resolve and
    the call is followed, and directories land outside the container
    before the pin gets its chance to refuse.

    A host path (no container prefix) is not walked component by
    component — the host filesystem is not the threat — but its parent is
    still opened, so callers get the same (dir_fd, leaf) pair either way.
    """
    name = container_from_spec(spec)
    fd = None
    try:
        try:
            if name is None:
                base = resolved if inside else (
                    os.path.dirname(resolved) or os.sep
                )
                leaf = "" if inside else os.path.basename(resolved)
                if create:
                    os.makedirs(base, exist_ok=True)
                fd = os.open(base, _O_DIR)
            else:
                rootfs = os.path.normpath(container_rootfs(name))
                rel = os.path.relpath(resolved, rootfs)
                parts = [] if rel == os.curdir else rel.split(os.sep)
                leaf = "" if inside else (parts.pop() if parts else "")
                fd = os.open(rootfs, _O_DIR)
                for part in parts:
                    fd = _descend(fd, part, create)
        except OSError as exc:
            if fd is not None:
                os.close(fd)
                fd = None
            if isinstance(exc, _Refused):
                # A component is a symlink now and was not at resolve
                # time — exactly the race this guards against.
                crit_error(
                    f"path '{spec}' changed while it was being resolved "
                    f"({exc.strerror}); refusing to continue."
                )
            else:
                crit_error(f"cannot open '{spec}': {exc.strerror}.")
            sys.exit(1)
        yield PinnedPath(resolved, fd, leaf)
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


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
