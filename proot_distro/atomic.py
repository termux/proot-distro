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
# The components below the enclosing trust root are therefore walked one
# at a time with O_NOFOLLOW (statedir.open_state_dir), the temporary is
# created O_EXCL off the descriptor that walk validated, and the
# publishing rename runs src_dir_fd/dst_dir_fd on it. A path outside
# those roots is the user's own (`build --output`, `backup -o`) and
# keeps the plain behaviour: it is not this program's business where the
# user points it.
#
# publish_file() is the same ending without the beginning, for a file
# whose final name cannot be known until its bytes exist -- a build's
# layer blob is named by the digest of its own content, so it is written
# to the build's scratch directory first and renamed into the cache
# afterwards. The destination directory is reached the same way.
#
# What the caller writes through is the **descriptor** of the temporary,
# never its name. Handing back a path and letting the caller open it
# again put a window between the create and that open, and a name is not
# a secret: an unpredictable name cannot be waited for, but it can be
# *seen* -- a process sharing the directory reads it out of readdir(),
# unlinks it and puts a symlink in its place, and the caller's open()
# then writes the file's bytes into whatever that names. The rename
# afterwards publishes the symlink, so the cache entry ends up pointing
# at the host file it just overwrote. On Termux the directory really is
# shared: RUNTIME_DIR and BASE_CACHE_DIR both sit under the
# $TERMUX_PREFIX bound read-write into every non-isolated container.
#
# The descriptor settles which inode the bytes go into, and the rename
# publishes that same inode -- it is created O_EXCL and never named
# again, so there is nothing left for a swap to redirect. atomic_write()
# is the same thing with the descriptor already wrapped in a file
# object, which is what most callers want.

import contextlib
import errno
import os
import tempfile

from proot_distro import dirfd, statedir


def _state_location(path: str):
    """Return (root, parts) when *path* is inside a state dir, else (None, None).

    A root itself is a directory, not a destination to write, so it
    comes back as "not in the state tree" and takes the plain branch --
    which cannot happen for a real caller, every one of which names a
    file below a root.
    """
    root, parts = statedir.split_state_path(path)
    if root is None or not parts:
        return None, None
    return root, parts


@contextlib.contextmanager
def atomic_replace(path: str, *, suffix: str = ".tmp"):
    """Yield an open fd on a tmp next to *path*; rename it on success.

    The caller writes through the descriptor — with os.write(), or by
    wrapping it in ``open(fd, mode, closefd=False)``; atomic_write()
    below does the wrapping. The descriptor stays this context
    manager's to close. On normal exit the tmp is os.replace()'d onto
    *path* (atomic on POSIX). On any exception the tmp is removed and
    the original exception re-raised.

    A **descriptor** rather than the tmp's name, because a name the
    caller has to open again is a name something else can put a symlink
    under in between — see the note at the top of this module.

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

    dir_fd = statedir.open_state_dir(
        os.path.join(root, *parts[:-1]), create=True,
    )
    try:
        yield from _replace_at(dir_fd, parts[-1], suffix)
    finally:
        os.close(dir_fd)


@contextlib.contextmanager
def atomic_write(path: str, mode: str = "wb", *, suffix: str = ".tmp",
                 **kwargs):
    """atomic_replace() with the descriptor already wrapped in a file.

    The shape nearly every caller wants: a file object to json.dump(),
    write() or copyfileobj() into. The wrapper is closed (and so
    flushed) before the rename, and it does not own the descriptor —
    atomic_replace() closes that.
    """
    with atomic_replace(path, suffix=suffix) as fd:
        with open(fd, mode, closefd=False, **kwargs) as fh:
            yield fh


def publish_file(src_path: str, dest_path: str) -> None:
    """Rename an already-written file onto *dest_path*.

    For a writer that cannot name its destination up front: a layer blob
    is named by the digest of its own bytes, so it is packed into the
    build's scratch directory and published once the digest is known.
    os.makedirs(os.path.dirname(dest)) followed by os.replace(tmp, dest)
    resolved the destination directory by name twice, so a guest that
    left `cache/oci_layers -> <host dir>` behind collected every layer a
    build produced. The directory is walked down to with O_NOFOLLOW
    instead and the rename runs dst_dir_fd on the descriptor that walk
    validated. rename(2) follows no symlink at the destination name
    either, so a link planted *as* the blob is replaced, not written
    through.

    A destination outside the state tree is the user's own and keeps the
    plain behaviour.
    """
    root, parts = _state_location(dest_path)
    if root is None:
        dest_dir = os.path.dirname(dest_path) or "."
        os.makedirs(dest_dir, exist_ok=True)
        os.replace(src_path, dest_path)
        return

    dir_fd = statedir.open_state_dir(
        os.path.join(root, *parts[:-1]), create=True,
    )
    try:
        os.replace(src_path, parts[-1], dst_dir_fd=dir_fd)
    finally:
        os.close(dir_fd)


def _close_quietly(fd: int) -> None:
    """Close *fd*, tolerating a caller that already closed it."""
    try:
        os.close(fd)
    except OSError:
        pass


def _replace_at(dir_fd: int, name: str, suffix: str):
    """atomic_replace's body for a destination directory already validated."""
    tmp_name = dirfd.temp_name(
        name, f".{os.getpid()}.{os.urandom(4).hex()}{suffix}",
    )
    # O_RDWR, matching what mkstemp gives the by-name branch: a writer
    # that must read its own bytes back (a layer blob handed to the
    # extractor) does it through this descriptor rather than by opening
    # the name a second time.
    fd, _st = dirfd.open_new_at(dir_fd, tmp_name, 0o600, readable=True)
    try:
        yield fd
        written = os.fstat(fd)
        os.close(fd)
        fd = None
        _publish_at(dir_fd, tmp_name, name, written)
    except BaseException:
        if fd is not None:
            _close_quietly(fd)
        dirfd.unlink_quietly(dir_fd, tmp_name)
        raise


def _publish_at(dir_fd: int, tmp_name: str, name: str, written) -> None:
    """rename(2) the temporary onto *name*, refusing one that was swapped.

    The bytes went into the inode *written* describes and can no longer
    be redirected, but the rename is still by name, so a process sharing
    the directory can unlink the temporary and leave a symlink under it:
    the write lands where it should and the *published* entry is the
    attacker's link. Nothing downstream would read it (every consumer of
    these files opens through dirfd.open_regular_at, which refuses one),
    but publishing something this module did not write is not a thing to
    do quietly either.

    Comparing the entry against the descriptor's identity leaves only
    the instant between this lstat and the rename, in place of the whole
    duration of the write -- which for a layer blob is however long the
    download takes. A mismatch aborts, and the caller's cleanup unlinks
    whatever is standing there.
    """
    try:
        entry = dirfd.lstat_at(dir_fd, tmp_name)
    except OSError:
        entry = None
    if (entry is None
            or (entry.st_dev, entry.st_ino)
            != (written.st_dev, written.st_ino)):
        raise OSError(
            errno.ESTALE,
            "the temporary file was replaced while it was being written",
            tmp_name,
        )
    os.replace(tmp_name, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)


def _replace_by_name(path: str, suffix: str):
    """atomic_replace's body for a path the user chose, not the program."""
    dest_dir = os.path.dirname(path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".",
        suffix=suffix,
        dir=dest_dir,
    )
    try:
        yield fd
        os.close(fd)
        fd = None
        os.replace(tmp, path)
    except BaseException:
        if fd is not None:
            _close_quietly(fd)
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
