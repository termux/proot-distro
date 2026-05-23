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

import errno
import fcntl
import hashlib
import os
import sys

from proot_distro.constants import RUNTIME_DIR
from proot_distro.message import crit_error

LOCKS_DIR = os.path.join(RUNTIME_DIR, "locks")
_BUILD_LOCKS_DIR = os.path.join(LOCKS_DIR, "build")

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


def read_lock_info(lock_path: str) -> str:
    """Return a human-readable hint about who holds the lock, or ''.

    Reads the lock file's first line (PID + command name) and returns
    a parenthesised note suitable for appending to an error message.
    Returns '' when the file is missing, empty, or names a dead PID.
    """
    try:
        with open(lock_path) as fh:
            line = fh.readline().strip()
        if not line:
            return ""
        parts = line.split(None, 1)
        pid_str = parts[0]
        cmd = parts[1] if len(parts) > 1 else "unknown"
        try:
            pid = int(pid_str)
            os.kill(pid, 0)
            return f" (PID {pid}: {cmd})"
        except (OSError, ValueError):
            return ""
    except OSError:
        return ""


class _FlockBase:
    """Shared flock(2) machinery for the lock classes below.

    Subclasses set self._lock_path (absolute), self._label (the noun
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
        # Subclasses populate these before acquire() is called.
        self._lock_path: str = ""
        self._label: str = "resource"
        self._display: str = ""

    @property
    def lock_path(self) -> str:
        return self._lock_path

    def acquire(self) -> bool:
        """Try to acquire the lock non-blocking.

        Returns True on success (or when re-entrant / filesystem ignores
        flock). Returns False when blocked by another process.
        """
        if self._lock_path in _held_exclusive:
            # This process already holds an exclusive lock on this path
            # — any lock type requested by the caller is satisfied.
            self._reentrant = True
            return True

        try:
            os.makedirs(os.path.dirname(self._lock_path), exist_ok=True)
        except OSError:
            return True  # Cannot create locks dir; proceed unlocked.

        try:
            fd = open(self._lock_path, "w")
        except OSError:
            return True  # Cannot open/create lock file; proceed unlocked.

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

        # Record PID + command in the file for diagnostic purposes.
        try:
            fd.write(f"{os.getpid()} {self._command}\n")
            fd.flush()
        except OSError:
            pass

        self._fd = fd
        if self._exclusive:
            _held_exclusive.add(self._lock_path)
        return True

    def release(self) -> None:
        """Release the lock. No-op when re-entrant or not yet acquired."""
        if self._reentrant:
            return
        if self._exclusive:
            _held_exclusive.discard(self._lock_path)
        if self._fd is not None:
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
            hint = read_lock_info(self._lock_path)
            crit_error(f"{self._label} '{self._display}' is busy{hint}.")
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
        self._label = "image"
        self._display = f"{image_ref} ({arch})"
