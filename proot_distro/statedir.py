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

# Architecture: one way to reach a directory of the program's own state
# tree -- containers/, cache/, build-tmp/ and everything under them --
# and it is not by name.
#
# RUNTIME_DIR and BASE_CACHE_DIR are the two trust roots. They are named
# once, the way every module names them, and created by name when they
# are missing: a first run on a machine that has never had this program
# installed must not fail because its own state directory does not exist
# yet. Everything *below* a root is guest content on Termux, where both
# roots sit under the $TERMUX_PREFIX bound read-write into every
# non-isolated container, so a session can leave `containers/<name>`,
# `cache` or `build-tmp` behind as a symlink to any directory the user
# can write. Naming such a path is enough to have this program write --
# or delete -- inside whatever it leads to: os.makedirs(exist_ok=True)
# accepts a symlink to a directory, open() follows one, and so does the
# opendir() a removal walk starts from.
#
# So the components below the root are walked one at a time with
# O_NOFOLLOW (dirfd.opendir_at), and the caller gets the descriptor the
# walk validated rather than the path it validated. A component that is
# a symlink, or is not a directory at all, raises ENOTDIR instead of
# being followed.
#
# What this cannot do is settle what happens to a *name* afterwards. A
# caller that keeps addressing entries as (dir_fd, name) is proof against
# the directory being re-pointed later; one that goes back to composing
# paths -- an extractor, proot resolving a bind source -- is proof only
# against the persistent case, where the link is already there when the
# command starts. That case is the one a guest can actually arrange at
# leisure, and it is the one this closes.

import errno
import os

from proot_distro import dirfd
from proot_distro.constants import BASE_CACHE_DIR, RUNTIME_DIR

# Shortest first: on Termux BASE_CACHE_DIR lives *under* RUNTIME_DIR, so
# matching the outer root is what puts `cache` itself inside the walk
# rather than in the part taken on trust. Off Termux the two are
# unrelated (XDG data vs cache) and at most one of them ever matches.
STATE_ROOTS = tuple(sorted({RUNTIME_DIR, BASE_CACHE_DIR}, key=len))

# What one of this program's own JSON documents may cost to read.
#
# A container's manifest.json, a session record, the build-cache index
# and a manifest-cache entry are all written here and are a few kilobytes
# at most -- but the *file* is a stranger's to replace. On Termux the
# whole state tree sits under the $TERMUX_PREFIX bound read-write into
# every non-isolated container, so a running guest decided how many bytes
# `login`, `run`, `ps`, `list --image`, `clear-cache` and `build` each
# pulled into memory before finding out the document was nonsense:
# json.load() on the descriptor reads until the file ends. 16 MiB is the
# ceiling install_local puts on an OCI archive's JSON and the one the
# registry side reads metadata through, and it is orders of magnitude
# above anything written here.
MAX_STATE_JSON_BYTES = 16 * 1024 * 1024

_READ_CHUNK = 1 << 20


def read_state_file(fd: int, *, limit: int = MAX_STATE_JSON_BYTES) -> bytes:
    """The whole content of the open state file *fd*, capped at *limit*.

    OSError(EFBIG) for a file holding more than *limit* bytes, which
    every caller already answers the way it answers an unreadable file
    -- and a document this program did not write is what an oversized
    one is.

    The cap is applied to the bytes actually drawn rather than to an
    fstat's st_size: the size a file reports is not a promise about how
    much can be read out of it, and one being appended to while it is
    read would otherwise pass the check and then exceed it.

    The descriptor stays the caller's to close.
    """
    chunks = []
    remaining = limit + 1
    while remaining > 0:
        chunk = os.read(fd, min(remaining, _READ_CHUNK))
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    data = b"".join(chunks)
    if len(data) > limit:
        raise OSError(
            errno.EFBIG,
            f"state file is larger than {limit} bytes; refusing to read it",
        )
    return data


def split_state_path(path: str):
    """Return (root, parts) for a path at or inside a state root.

    *parts* is empty when *path* is a root itself, which is a directory
    to open rather than to walk down to. (None, None) means the path is
    not in the state tree at all -- a destination the user named
    (`backup -o`, `build --output`), which is not this program's
    business to second-guess.

    The path is normalised first, so no '..' can survive in the parts:
    one that would climb out of the root leaves the result outside it,
    and the answer is then (None, None) rather than a walk that opens
    '..' off a validated descriptor.
    """
    normalised = os.path.normpath(path)
    for root in STATE_ROOTS:
        trimmed = root.rstrip(os.sep)
        if normalised == trimmed:
            return root, ()
        prefix = trimmed + os.sep
        if normalised.startswith(prefix):
            parts = tuple(
                p for p in normalised[len(prefix):].split(os.sep) if p
            )
            return root, parts
    return None, None


def is_state_path(path: str) -> bool:
    """True when *path* is at or inside one of the trust roots."""
    return split_state_path(path)[0] is not None


def _refuse(path: str) -> OSError:
    """The error a component that must not be followed comes back as."""
    return OSError(
        errno.ENOTDIR,
        "not a directory inside the proot-distro state tree",
        path,
    )


def open_state_dir(path: str, *, create: bool = False) -> int:
    """Open the state directory *path*. Returns a descriptor.

    Every component below the enclosing trust root is opened O_NOFOLLOW
    off the descriptor of the level above, so a symlink -- or anything
    else that is not a plain directory -- standing in for one of them
    raises ENOTDIR rather than being followed. With create=True the
    missing levels are made on the way down, each with mkdirat off the
    descriptor the walk has already validated.

    Raises FileNotFoundError when a component is missing and create is
    False, ENOTDIR when one must not be followed, and whatever else the
    underlying calls report. The caller owns the descriptor.

    A path outside the state tree is a caller error, not a runtime one:
    this is the walk for directories the program itself owns, and the
    trust root is what makes the walk mean anything.
    """
    root, parts = split_state_path(path)
    if root is None:
        raise ValueError(f"{path!r} is not inside the proot-distro state tree")

    if create:
        try:
            os.makedirs(root, exist_ok=True)
        except OSError:
            pass                    # the open below reports what is there
    fd = dirfd.opendir(root)
    try:
        for depth, part in enumerate(parts, 1):
            try:
                nxt = dirfd.opendir_at(fd, part)
            except FileNotFoundError:
                if not create:
                    raise
                try:
                    os.mkdir(part, 0o777, dir_fd=fd)
                except FileExistsError:
                    pass            # lost a race with another writer
                try:
                    nxt = dirfd.opendir_at(fd, part)
                except OSError as exc:
                    if dirfd.is_refusal(exc):
                        raise _refuse(
                            os.path.join(root, *parts[:depth])
                        ) from None
                    raise
            except OSError as exc:
                if dirfd.is_refusal(exc):
                    # A symlink (ELOOP, or ENOTDIR for O_NOFOLLOW with
                    # O_DIRECTORY on Linux), or a plain file in the way.
                    raise _refuse(os.path.join(root, *parts[:depth])) from None
                raise
            os.close(fd)
            fd = nxt
        opened, fd = fd, None
        return opened
    finally:
        if fd is not None:
            os.close(fd)


def open_state_parent(path: str, *, create: bool = False):
    """Open the *parent* of a state path. Returns (descriptor, leaf name).

    For a caller that acts on the entry itself -- creating it, replacing
    it, removing it -- and so needs the (dir_fd, name) pair rather than a
    descriptor on the entry. Raises the same way open_state_dir does.
    """
    root, parts = split_state_path(path)
    if root is None or not parts:
        raise ValueError(f"{path!r} names no entry in the state tree")
    parent_fd = open_state_dir(os.path.join(root, *parts[:-1]), create=create)
    return parent_fd, parts[-1]


def remove_state_tree(path: str, *, on_error=None, on_remove=None) -> bool:
    """Remove a state-tree path and everything under it. True when gone.

    dirfd.remove_tree()'s counterpart for a tree this program keeps: the
    parent is reached by the O_NOFOLLOW walk instead of being opened by
    name, so a planted `containers` -- or `containers/<name>`, for a
    rootfs being replaced -- cannot aim the removal at a host directory.
    Everything below is walked as (dir_fd, name) by rmtree_at, as it
    already was.

    Never raises, like remove_tree: every caller is a cleanup path.
    """
    if on_error is None:
        def on_error(_rel, _exc):
            return None

    root, parts = split_state_path(path)
    if root is None or not parts:
        # Not ours to walk down to (or a root itself, which no caller
        # removes) -- leave it to the path-taking front door.
        return dirfd.remove_tree(path, on_error=on_error, on_remove=on_remove)

    try:
        parent_fd = open_state_dir(os.path.join(root, *parts[:-1]))
    except FileNotFoundError:
        return True                 # already gone, along with its parent
    except OSError as exc:
        on_error("", exc)
        return False
    try:
        return dirfd.rmtree_at(parent_fd, parts[-1], force=True,
                               on_error=on_error, on_remove=on_remove)
    finally:
        os.close(parent_fd)
