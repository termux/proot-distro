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

# Advisory file-based locking for proot-distro.
#
# Two distinct lock namespaces:
#
#   ContainerLock — keyed by container name. Used by every command that
#     touches a container's rootfs.
#       Exclusive (write): install, restore, remove, rename, reset,
#                          copy/sync when destination is a container.
#       Shared (read):    backup, login, run,
#                          copy/sync when source is a container.
#     Multiple shared locks on the same name coexist freely. An
#     exclusive lock blocks all other locks (shared or exclusive) on
#     the same name.
#
#   BuildLock — keyed by (image_ref, arch). Used by `build` and `push`
#     to prevent concurrent operations on the same image tag, which
#     would race on the manifest cache, the build-cache index, and any
#     --output file. Always exclusive.
#
# The two namespaces never collide because BuildLock files live under
# RUNTIME_DIR/locks/build/ while ContainerLock files live directly under
# RUNTIME_DIR/locks/.
#
# Implementation: POSIX flock(2). Locks are non-blocking — if
# acquisition fails the command exits immediately.
#
# For 'login' and 'run', which replace the process via os.execvpe(), the
# lock file descriptor is made inheritable so proot inherits it and the
# lock is held for the entire container session. Python sets O_CLOEXEC
# on all newly opened fds by default (PEP 446), so we explicitly clear
# that flag when inheritable=True is requested.
#
# Re-entrancy: _held_exclusive tracks lock-file paths this process
# currently holds exclusively. When command_reset calls command_install
# for the same name, install finds the path already recorded and skips
# re-acquisition.
#
# Every lock file is addressed as (directory fd, entry name), never as a
# path. RUNTIME_DIR/locks is guest-writable -- on Termux it sits under the
# $TERMUX_PREFIX bound read-write into every non-isolated container -- and
# the names in it are predictable, so open(path, "w") followed a planted
# `<name>.lock -> /host/file` and truncated that file before flocking
# anything. O_NOFOLLOW refuses the link instead; since nothing but this
# module writes here, an entry that is not a plain file was planted, and
# it is unlinked and the real lock file made in its place. One that will
# not go is one of the two cases that fail closed: a filesystem that
# cannot hold a lock file at all still proceeds unlocked, as it always
# has, but a lock this program is being *prevented* from taking must not
# pass for one it merely could not create.
#
# The other is being denied access outright. The lock directory and the
# names in it are the guest's to chmod, so `chmod 000` on locks/, on
# RUNTIME_DIR, or on one <name>.lock made every EACCES look like a
# filesystem that cannot hold locks -- and the command carried on
# unlocked, which is how a container arranged for `remove`, `restore`,
# `reset`, `copy` or `sync` to run alongside its own live session.
# EACCES/EPERM anywhere on the lock path is therefore a refusal, while
# EROFS, ENOSPC and a filesystem that ignores flock keep the old
# behaviour: those say the lock file cannot exist, not that someone is
# keeping this process away from it.

import errno
import fcntl
import hashlib
import os
import sys

from proot_distro import dirfd
from proot_distro.constants import RUNTIME_DIR
from proot_distro.message import crit_error

LOCKS_DIR = os.path.join(RUNTIME_DIR, "locks")
_BUILD_LOCKS_DIR = os.path.join(LOCKS_DIR, "build")

# The two lock directories as component lists, relative to RUNTIME_DIR:
# what the O_NOFOLLOW walk below descends, one level at a time.
_CONTAINER_PARTS = ("locks",)
_BUILD_PARTS = ("locks", "build")

# Absolute lock-file paths for which this process currently holds an
# exclusive flock. Used to make exclusive locking re-entrant within a
# single invocation.
_held_exclusive: set = set()


def container_lock_path(name: str) -> str:
    """Return the lock-file path for the container named *name*."""
    return os.path.join(LOCKS_DIR, f"{name}.lock")


def _build_lock_path(image_ref: str, arch: str) -> str:
    """Return the lock-file path for a build of (image_ref, arch).

    The key matches the manifest-cache key (16-hex-char sha256 prefix)
    so a build lock identifies the same artifact the build writes to.
    """
    key = hashlib.sha256(f"{image_ref}_{arch}".encode()).hexdigest()[:16]
    return os.path.join(_BUILD_LOCKS_DIR, f"{key}.lock")


def _locks_dir_fd(parts, create: bool = False):
    """Open one of the two lock directories. Descriptor, or None.

    RUNTIME_DIR is the trust root — the program's own state directory,
    named the same way every other module names it — and everything below
    it is walked with O_NOFOLLOW, so a `locks` (or `locks/build`) symlink
    a guest left behind sends nothing into a host directory. The root
    itself is still created by name: a first `install` on a machine that
    has never run this program must not proceed unlocked merely because
    RUNTIME_DIR does not exist yet.

    Creating goes level by level so a planted level gets the same
    treatment a planted lock file does — replaced, or refused. Reading
    (busy_locks, a holder hint) touches nothing.

    None is "there is no lock directory and none could be made", which
    has always meant "carry on unlocked". Being *kept* from one is
    _LockPathDenied instead: a `chmod 000` on RUNTIME_DIR or on locks/
    is a thing a container can do, and it must not read as a filesystem
    that cannot hold locks. Reading raises it too, since a directory
    that cannot be listed hides holders rather than proving there are
    none; there, a missing directory is the only "nothing is held".
    """
    if not create:
        return _read_locks_dir_fd(parts)

    try:
        os.makedirs(RUNTIME_DIR, exist_ok=True)
    except OSError as exc:
        if _is_denied(exc):
            raise _LockPathDenied(RUNTIME_DIR) from None
        return None
    try:
        fd = dirfd.opendir(RUNTIME_DIR)
    except OSError as exc:
        if _is_denied(exc):
            raise _LockPathDenied(RUNTIME_DIR) from None
        return None
    try:
        for depth, part in enumerate(parts, 1):
            nxt = _open_lock_subdir(
                fd, part, os.path.join(RUNTIME_DIR, *parts[:depth]),
            )
            if nxt is None:
                return None
            os.close(fd)
            fd = nxt
        opened, fd = fd, None
        return opened
    finally:
        if fd is not None:
            os.close(fd)


def _read_locks_dir_fd(parts):
    """Open one of the lock directories without creating anything.

    None means the directory is simply not there, which is the one shape
    of "no lock is held" a reader may believe. Anything else -- no
    permission, a planted symlink where the directory should be, a
    component that is not a directory -- hides whatever is held behind
    it, so it comes back as LockStateUnknown rather than as an empty
    answer.
    """
    try:
        root_fd = dirfd.opendir(RUNTIME_DIR)
    except FileNotFoundError:
        return None
    except OSError:
        raise LockStateUnknown(RUNTIME_DIR) from None
    try:
        return dirfd.descend_at(root_fd, parts)
    except FileNotFoundError:
        return None
    except OSError:
        raise LockStateUnknown(
            os.path.join(RUNTIME_DIR, *parts)
        ) from None
    finally:
        os.close(root_fd)


def _open_lock_subdir(dir_fd: int, name: str, path: str):
    """Open (creating) the lock directory *name* under dir_fd. Or None."""
    try:
        return dirfd.opendir_at(dir_fd, name)
    except FileNotFoundError:
        pass
    except OSError as exc:
        if _is_denied(exc):
            raise _LockPathDenied(path) from None
        if not _is_planted(exc):
            return None
        if not _drop_planted(dir_fd, name):
            raise _HostileLockPath(path) from None
    try:
        os.mkdir(name, 0o777, dir_fd=dir_fd)
    except FileExistsError:
        pass                        # lost a race with another writer
    except OSError as exc:
        if _is_denied(exc):
            raise _LockPathDenied(path) from None
        return None
    try:
        return dirfd.opendir_at(dir_fd, name)
    except OSError as exc:
        if _is_planted(exc):
            raise _HostileLockPath(path) from None
        if _is_denied(exc):
            raise _LockPathDenied(path) from None
        return None


def _lock_info_at(dir_fd: int, name: str) -> str:
    """Return a human-readable hint about who holds the lock, or ''.

    Reads the lock file's first line (PID + command name) and returns
    a parenthesised note suitable for appending to an error message.
    Returns '' when the file is missing, empty, or names a dead PID.
    """
    try:
        fd, _st = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    except OSError:
        return ""
    try:
        fh = os.fdopen(fd, "r", errors="replace")
    except OSError:
        try:
            os.close(fd)
        except OSError:
            pass
        return ""
    try:
        with fh:
            line = fh.readline().strip()
    except OSError:
        return ""
    if not line:
        return ""
    parts = line.split(None, 1)
    pid_str = parts[0]
    cmd = parts[1] if len(parts) > 1 else "unknown"
    try:
        pid = int(pid_str)
        os.kill(pid, 0)
    except (OSError, ValueError):
        return ""
    return f" (PID {pid}: {cmd})"


def _lock_is_held_at(dir_fd: int, name: str, path: str = "") -> bool:
    """Return True iff some process holds *name* exclusively.

    Shared, non-blocking flock probe — the same one session.py uses to
    tell a live session from a dead one. A refusal means an exclusive
    holder is present; success means the file is unheld, and the shared
    lock is dropped again immediately rather than held across any work.
    Any other errno from the flock is treated as "not held", matching
    acquire()'s rule that a filesystem which ignores flock must not
    stall the caller.

    Failing to *open* the entry is a different matter. Only its absence
    proves nobody holds it: a holder keeps a plain file open under this
    very name, so a `chmod 000` or a FIFO left in its place hides one
    rather than ruling it out, and this probe is what an orphan sweep
    asks before deleting. Both come back as LockStateUnknown.
    """
    try:
        fd, _st = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    except FileNotFoundError:
        return False
    except OSError:
        raise LockStateUnknown(path or name) from None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            return exc.errno in (errno.EACCES, errno.EAGAIN)
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _probe_locks_dir(parts, held: list) -> None:
    """Append (path, hint) for every held lock in one lock directory."""
    dir_fd = _locks_dir_fd(parts)
    if dir_fd is None:
        return
    try:
        try:
            names = dirfd.listdir_at(dir_fd)   # already sorted
        except OSError:
            raise LockStateUnknown(
                os.path.join(RUNTIME_DIR, *parts)
            ) from None
        for name in names:
            if not name.endswith(".lock"):
                continue
            path = os.path.join(RUNTIME_DIR, *parts, name)
            if _lock_is_held_at(dir_fd, name, path):
                held.append((path, _lock_info_at(dir_fd, name)))
    finally:
        os.close(dir_fd)


def busy_locks() -> list:
    """Return (lock_path, hint) for every lock another process holds.

    Both namespaces are scanned, so one call covers every command that
    writes to the download cache: `install` (and `reset`, through it)
    takes an exclusive ContainerLock, `build` and `push` an exclusive
    BuildLock. Shared holders — a `login` session, a running `backup` —
    do not answer the probe and are deliberately absent from the result:
    they never touch the cache.

    The answer is a snapshot by construction. It says nothing about a
    command that starts immediately afterwards, so it is a guard against
    running concurrently with work in progress, not a lock.

    Raises LockStateUnknown when any part of the lock tree cannot be
    read. An empty list has to mean "nothing is held", and a directory
    or an entry this process is not allowed to look at cannot say that
    -- a container is free to chmod either, and the caller deletes on
    the strength of the answer.
    """
    held = []
    for parts in (_CONTAINER_PARTS, _BUILD_PARTS):
        _probe_locks_dir(parts, held)
    return held


class _HostileLockPath(Exception):
    """Raised when the lock file's name is occupied by something else."""


class _LockPathDenied(Exception):
    """Raised when the lock path cannot be reached: permission denied.

    A lock directory or lock file this program creates itself is
    readable and writable by the user running it, so an EACCES on one
    is not the state a working installation is ever in -- but it is
    exactly the state a `chmod 000` from inside a container leaves it
    in, and RUNTIME_DIR/locks is guest-writable on Termux. Carrying on
    unlocked there is how a live session arranged to be raced.
    """


class LockStateUnknown(Exception):
    """Raised when busy_locks() cannot tell whether a lock is held.

    "Nothing is held" and "this process is not allowed to look" are
    different answers, and only the first is safe to act on: the caller
    (clear-cache's orphan sweep) is about to delete blobs on the strength
    of no build being in progress.
    """


def _open_lock_file(dir_fd: int, name: str, path: str):
    """Open (creating) the lock file *name* under dir_fd. Or None.

    O_NOFOLLOW plus open_regular_at()'s type check, so neither a symlink
    nor a FIFO under the name is opened — the first would truncate a host
    file, the second would block the command until a peer that never
    comes. Either is an entry no writer of this directory could have
    made, so it is dropped and the real lock file created; if it cannot
    be dropped, _HostileLockPath says so rather than letting the caller
    treat it as an ordinary "cannot create" and proceed unlocked.

    None is that ordinary case: a read-only filesystem, no permission,
    a directory in the way. It has always meant "carry on without a
    lock" and still does.
    """
    flags = os.O_RDWR | os.O_CREAT
    try:
        fd, _st = dirfd.open_regular_at(dir_fd, name, flags, 0o644)
        return fd
    except OSError as exc:
        if _is_denied(exc):
            raise _LockPathDenied(path) from None
        if not _is_planted(exc):
            return None
    if not _drop_planted(dir_fd, name):
        raise _HostileLockPath(path)
    try:
        fd, _st = dirfd.open_regular_at(dir_fd, name, flags, 0o644)
        return fd
    except OSError as exc:
        if _is_planted(exc):
            raise _HostileLockPath(path) from None
        if _is_denied(exc):
            raise _LockPathDenied(path) from None
        return None


def open_lock_file_at(dir_fd: int, name: str, path: str = ""):
    """Open (creating) a lock file under dir_fd. Descriptor, or None.

    The public form of _open_lock_file, for a lock file this module does
    not own: the build-cache index keeps its own next to the index, which
    lives in the download cache rather than in RUNTIME_DIR/locks. The
    opening rules are the same -- O_NOFOLLOW plus a type check, and an
    entry that is not a plain file was planted, so it is dropped and the
    real lock file made in its place.

    The *policy* differs at one point. Here a name that cannot be cleared
    comes back as None, "carry on without a lock", because that is what
    the caller already does on a filesystem that ignores flock and what
    losing the lock costs there is a concurrent record()'s entry, not a
    corrupt file -- the index itself is published through
    atomic_replace(). A container lock is the other way round and fails
    closed; see acquire(). Being denied the name is the same bargain
    here, and for the same reason.
    """
    try:
        return _open_lock_file(dir_fd, name, path or name)
    except (_HostileLockPath, _LockPathDenied):
        return None


# What opening an existing entry that is not a plain file reports:
# ELOOP/ENOTDIR for a symlink (is_refusal), EISDIR for a directory, EINVAL
# from open_regular_at()'s own type check, ENXIO for a FIFO with no reader.
_PLANTED_ERRNOS = frozenset((errno.EISDIR, errno.EINVAL, errno.ENXIO))


def _is_planted(exc: OSError) -> bool:
    """True when *exc* says the name is held by something not a plain file."""
    return dirfd.is_refusal(exc) or exc.errno in _PLANTED_ERRNOS


# What being kept away from the lock path reports. EROFS, ENOSPC and
# friends are deliberately absent: they say the lock file cannot exist,
# which has always meant "carry on unlocked", not that something is
# standing between this process and it.
_DENIED_ERRNOS = frozenset((errno.EACCES, errno.EPERM))


def _is_denied(exc: OSError) -> bool:
    """True when *exc* says this process is not allowed at the lock path."""
    return exc.errno in _DENIED_ERRNOS


def _drop_planted(dir_fd: int, name: str) -> bool:
    """Remove whatever occupies *name*. True once the name is free.

    A directory needs rmdir and so is only removable while empty, which
    is the one shape of this that stays in the way. Everything else --
    a symlink, a FIFO, a socket, a device node -- unlinks.
    """
    try:
        os.unlink(name, dir_fd=dir_fd)
        return True
    except FileNotFoundError:
        return True
    except OSError as exc:
        # Removing a directory by unlink(2) is EISDIR on Linux, EPERM
        # where POSIX leaves the choice open.
        if exc.errno not in (errno.EISDIR, errno.EPERM):
            return False
    try:
        os.rmdir(name, dir_fd=dir_fd)
        return True
    except OSError:
        return False


class _FlockBase:
    """Shared flock(2) machinery for the lock classes below.

    Subclasses set self._lock_path (absolute), self._dir_parts (its
    directory as components below RUNTIME_DIR), self._label (the noun
    used in the conflict error, e.g. 'container' or 'image'), and
    self._display (the resource identifier shown to the user).
    """

    def __init__(
        self,
        exclusive: bool,
        command: str,
        inheritable: bool,
    ) -> None:
        self._exclusive = exclusive
        self._command = command
        self._inheritable = inheritable
        self._fd = None
        self._reentrant = False
        self._disowned = False
        self._hostile = ""
        self._denied = ""
        # Subclasses populate these before acquire() is called.
        self._lock_path: str = ""
        self._dir_parts: tuple = _CONTAINER_PARTS
        self._label: str = "resource"
        self._display: str = ""

    @property
    def lock_path(self) -> str:
        return self._lock_path

    def holder_hint(self) -> str:
        """Parenthesised note naming the lock's holder, or ''.

        Cosmetic, so a lock tree it cannot read is simply no hint -- the
        refusal itself is decided in acquire(), which does not fall back
        to a guess.
        """
        try:
            dir_fd = _locks_dir_fd(self._dir_parts)
        except LockStateUnknown:
            return ""
        if dir_fd is None:
            return ""
        try:
            return _lock_info_at(dir_fd, os.path.basename(self._lock_path))
        finally:
            os.close(dir_fd)

    def blocked_detail(self) -> str:
        """Why acquire() refused, beyond another process holding the lock.

        Empty when the lock is simply held by someone else, which is the
        ordinary conflict every caller already words for itself.
        """
        if self._hostile:
            return (f"'{self._hostile}' is not a plain file and could "
                    f"not be replaced, so this command cannot be "
                    f"serialised against others. Remove it and try "
                    f"again.")
        if self._denied:
            return (f"'{self._denied}' cannot be opened (permission "
                    f"denied), so this command cannot be serialised "
                    f"against others. Restore access to it and try "
                    f"again.")
        return ""

    def acquire(self) -> bool:
        """Try to acquire the lock non-blocking.

        Returns True on success (or when re-entrant / filesystem ignores
        flock). Returns False when blocked by another process, when the
        lock file's name is occupied by something this module cannot
        remove (see _open_lock_file), or when the lock path cannot be
        reached at all because access to it is denied — a state a
        container can arrange with one chmod, and the reason that is not
        treated as "this filesystem cannot hold locks". blocked_detail()
        tells them apart.
        """
        if self._lock_path in _held_exclusive:
            # This process already holds an exclusive lock on this path
            # — any lock type requested by the caller is satisfied.
            self._reentrant = True
            return True

        try:
            dir_fd = _locks_dir_fd(self._dir_parts, create=True)
            if dir_fd is None:
                return True  # Cannot create locks dir; proceed unlocked.
            try:
                raw = _open_lock_file(
                    dir_fd, os.path.basename(self._lock_path), self._lock_path,
                )
            finally:
                os.close(dir_fd)
        except _HostileLockPath as exc:
            self._hostile = str(exc)
            return False
        except _LockPathDenied as exc:
            self._denied = str(exc)
            return False
        if raw is None:
            return True  # Cannot open/create lock file; proceed unlocked.

        try:
            fd = os.fdopen(raw, "r+")
        except OSError:
            os.close(raw)
            return True

        if self._inheritable:
            try:
                os.set_inheritable(fd.fileno(), True)
            except OSError:
                pass

        lock_op = (
            (fcntl.LOCK_EX if self._exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB
        )
        try:
            fcntl.flock(fd.fileno(), lock_op)
        except OSError as exc:
            fd.close()
            if exc.errno in (errno.EACCES, errno.EAGAIN):
                return False
            return True  # Filesystem does not support flock; proceed unlocked.

        # Record PID + command in the file for diagnostic purposes. The
        # truncation happens *after* the flock, not as part of opening
        # the file: a process that loses the race must leave the holder's
        # line intact, or the conflict it is about to report names nobody.
        try:
            fd.seek(0)
            fd.truncate()
            fd.write(f"{os.getpid()} {self._command}\n")
            fd.flush()
        except OSError:
            pass

        self._fd = fd
        if self._exclusive:
            _held_exclusive.add(self._lock_path)
        return True

    def disown(self) -> None:
        """Keep the lock held after this process drops its handle.

        For detached sessions the lock fd is made inheritable and a
        forked descendant (which exec's into proot) holds the same open
        file description. flock(2) releases a lock on an explicit
        LOCK_UN of *any* duplicate fd, or once *all* duplicates are
        closed — so this process must close its fd WITHOUT issuing
        LOCK_UN. Marking the lock disowned makes release() skip the
        unlock; closing our fd alone then leaves the descendant's copy
        holding the lock for the whole session.
        """
        self._disowned = True

    def release(self) -> None:
        """Release the lock. No-op when re-entrant or not yet acquired.

        When disowned, the fd is closed but LOCK_UN is skipped so a
        forked descendant sharing the open file description keeps the
        lock held (see disown()).
        """
        if self._reentrant:
            return
        if self._exclusive:
            _held_exclusive.discard(self._lock_path)
        if self._fd is not None:
            if not self._disowned:
                try:
                    fcntl.flock(self._fd.fileno(), fcntl.LOCK_UN)
                except OSError:
                    pass
            try:
                self._fd.close()
            except OSError:
                pass
            self._fd = None

    def __enter__(self):
        if not self.acquire():
            detail = self.blocked_detail()
            if detail:
                crit_error(
                    f"cannot lock {self._label} '{self._display}': {detail}"
                )
            else:
                crit_error(f"{self._label} '{self._display}' is busy"
                           f"{self.holder_hint()}.")
            sys.exit(1)
        return self

    def __exit__(self, *_) -> None:
        self.release()


class ContainerLock(_FlockBase):
    """Advisory lock for a single container name.

    Usage as context manager::

        with ContainerLock("ubuntu", exclusive=True, command="install"):
            ...modify container...

    On conflict the process exits with an error immediately; it never waits.

    For login/run, pass inheritable=True so the lock fd is inherited by
    the proot process after os.execvpe() and held for the container session.
    """

    def __init__(
        self,
        name: str,
        exclusive: bool,
        command: str = "",
        inheritable: bool = False,
    ) -> None:
        super().__init__(
            exclusive=exclusive,
            command=command,
            inheritable=inheritable,
        )
        self._lock_path = container_lock_path(name)
        self._dir_parts = _CONTAINER_PARTS
        self._label = "container"
        self._display = name


class BuildLock(_FlockBase):
    """Advisory exclusive lock for a single (image_ref, arch) build target.

    Used by `build` and `push` to prevent concurrent operations on the
    same image tag from racing on the manifest cache, the build-cache
    index, and any --output file. The lock key matches the
    manifest-cache key so the lock identifies the same artifact the
    caller is about to read or write.

    Usage as context manager::

        with BuildLock("myrepo/myapp:1.0", "aarch64", command="build"):
            ...produce manifest + layers...
    """

    def __init__(
        self,
        image_ref: str,
        arch: str,
        command: str = "build",
    ) -> None:
        super().__init__(exclusive=True, command=command, inheritable=False)
        self._lock_path = _build_lock_path(image_ref, arch)
        self._dir_parts = _BUILD_PARTS
        self._label = "image"
        self._display = f"{image_ref} ({arch})"
