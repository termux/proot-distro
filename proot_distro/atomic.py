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

# Architecture: One context manager for the "write to a sibling .tmp
# file, then atomically rename" pattern that lives in every cache /
# layer / manifest writer in the codebase. Centralising it gives us a
# single place to:
#
#   - resolve the destination directory and create it if missing,
#   - mint a process-unique tmp filename so concurrent writers cannot
#     race on a shared <dest>.tmp,
#   - guarantee the tmp file is removed when the with-block exits
#     unsuccessfully (including KeyboardInterrupt, so a Ctrl-C never
#     leaves a half-written sentinel behind).
#
# A destination inside the program's own state directories gets that
# through a descriptor. os.makedirs(exist_ok=True) accepts a symlink to
# a directory and tempfile.mkstemp(dir=...) then resolves the same name,
# so a guest leaving `cache/oci_layers -> <host dir>` behind had every
# blob written -- and renamed into place -- inside that host directory.
# RUNTIME_DIR and BASE_CACHE_DIR are guest-writable on Termux, where
# both sit under the $TERMUX_PREFIX bound read-write into every
# non-isolated container. The components below whichever root contains
# the destination are therefore walked one at a time with O_NOFOLLOW,
# the temporary is created O_EXCL off the descriptor that walk
# validated, and the publishing rename runs src_dir_fd/dst_dir_fd on it.
# A path outside those roots is the user's own (`build --output`,
# `backup -o`) and keeps the plain behaviour: it is not this program's
# business where the user points it.
#
# The tmp *path* is still what the caller writes through, since it opens
# the file itself in whatever way suits it. That name is unpredictable
# and was just created, so nothing can be waiting under it; a directory
# re-pointed in the window between would strand those bytes under a
# random name and fail the rename, rather than publish them somewhere
# else.

import contextlib
import errno
import os
import tempfile

from proot_distro import dirfd
from proot_distro.constants import BASE_CACHE_DIR, RUNTIME_DIR

# Shortest first: on Termux BASE_CACHE_DIR lives *under* RUNTIME_DIR, so
# matching the outer root is what puts `cache` itself inside the walk
# rather than in the part taken on trust. Off Termux the two are
# unrelated (XDG data vs cache) and at most one of them ever matches.
_STATE_ROOTS = tuple(sorted({RUNTIME_DIR, BASE_CACHE_DIR}, key=len))


def _state_location(path: str):
    """Return (root, parts) when *path* is inside a state dir, else (None, None)."""
    for root in _STATE_ROOTS:
        prefix = root.rstrip(os.sep) + os.sep
        if path.startswith(prefix):
            parts = tuple(p for p in path[len(prefix):].split(os.sep) if p)
            if parts:
                return root, parts
    return None, None


@contextlib.contextmanager
def atomic_replace(path: str, *, suffix: str = ".tmp"):
    """Yield a tmp path next to *path*; rename on success, remove on error.

    The caller writes to the yielded tmp path however it pleases —
    open()/tarfile.open()/shutil.copyfileobj are all fine. On normal
    exit the tmp is os.replace()'d onto *path* (atomic on POSIX). On
    any exception the tmp is removed and the original exception
    re-raised.

    A unique tmp name is minted per call so two concurrent writers to
    the same final path (e.g. two `build`s sharing a base image)
    cannot collide on a sentinel.

    A destination inside RUNTIME_DIR or BASE_CACHE_DIR is reached by an
    O_NOFOLLOW walk from that root rather than by name, and a component
    that is not a plain directory raises ENOTDIR instead of being
    followed — see the note at the top of this module.
    """
    root, parts = _state_location(path)
    if root is None:
        yield from _replace_by_name(path, suffix)
        return

    try:
        os.makedirs(root, exist_ok=True)
    except OSError:
        pass                        # the walk below reports what it finds
    dir_fd = dirfd.opendir_under(root, parts[:-1], create=True)
    if dir_fd is None:
        raise OSError(
            errno.ENOTDIR,
            "not a directory inside the proot-distro state tree",
            os.path.dirname(path),
        )
    try:
        yield from _replace_at(dir_fd, os.path.dirname(path),
                               parts[-1], suffix)
    finally:
        os.close(dir_fd)


def _replace_at(dir_fd: int, dest_dir: str, name: str, suffix: str):
    """atomic_replace's body for a destination directory already validated."""
    tmp_name = dirfd.temp_name(
        name, f".{os.getpid()}.{os.urandom(4).hex()}{suffix}",
    )
    fd, _st = dirfd.open_new_at(dir_fd, tmp_name, 0o600)
    os.close(fd)
    try:
        yield os.path.join(dest_dir, tmp_name)
        os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except BaseException:
        dirfd.unlink_quietly(dir_fd, tmp_name)
        raise


def _replace_by_name(path: str, suffix: str):
    """atomic_replace's body for a path the user chose, not the program."""
    dest_dir = os.path.dirname(path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".",
        suffix=suffix,
        dir=dest_dir,
    )
    os.close(fd)
    try:
        yield tmp
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
