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

# Advisory file-based locking for proot-distro containers.
#
# Locking rules:
#   - Exclusive (write): install, restore, remove, rename, reset,
#                        copy/sync when destination is a container.
#   - Shared (read):    backup, login, run,
#                        copy/sync when source is a container.
#
# Multiple shared locks on the same name coexist freely. An exclusive lock
# blocks all other locks (shared or exclusive) on the same name.
#
# Implementation: POSIX flock(2) on RUNTIME_DIR/locks/<name>.lock.
# Locks are non-blocking — if acquisition fails the command exits immediately.
#
# For 'login' and 'run', which replace the process via os.execvpe(), the lock
# file descriptor is made inheritable so proot inherits it and the lock is
# held for the entire container session. Python sets O_CLOEXEC on all newly
# opened fds by default (PEP 446), so we explicitly clear that flag when
# inheritable=True is requested.
#
# Re-entrancy: _held_exclusive tracks names this process currently holds
# exclusively. When command_reset calls command_install for the same name,
# install finds it already recorded and skips re-acquisition.

import errno
import fcntl
import os
import sys

from proot_distro.constants import RUNTIME_DIR
from proot_distro.colors import C, msg

LOCKS_DIR = os.path.join(RUNTIME_DIR, "locks")

# Names for which this process currently holds an exclusive flock.
# Used to make exclusive locking re-entrant within a single invocation.
_held_exclusive: set = set()


def _lock_path(name: str) -> str:
    return os.path.join(LOCKS_DIR, f"{name}.lock")


def _read_lock_info(lock_path: str) -> str:
    """Return a human-readable hint about who holds the lock, or ''."""
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


class ContainerLock:
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
        self._name = name
        self._exclusive = exclusive
        self._command = command
        self._inheritable = inheritable
        self._fd = None
        self._reentrant = False

    def acquire(self) -> bool:
        """Try to acquire the lock non-blocking.

        Returns True on success (or when re-entrant / filesystem ignores flock).
        Returns False when blocked by another process.
        """
        if self._name in _held_exclusive:
            # This process already holds an exclusive lock on this name —
            # any lock type requested by the caller is already satisfied.
            self._reentrant = True
            return True

        try:
            os.makedirs(LOCKS_DIR, exist_ok=True)
        except OSError:
            return True  # Cannot create locks dir; proceed unlocked.

        path = _lock_path(self._name)
        try:
            fd = open(path, "w")
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
            _held_exclusive.add(self._name)
        return True

    def release(self) -> None:
        """Release the lock. No-op when re-entrant or not yet acquired."""
        if self._reentrant:
            return
        if self._exclusive:
            _held_exclusive.discard(self._name)
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

    def __enter__(self) -> "ContainerLock":
        if not self.acquire():
            hint = _read_lock_info(_lock_path(self._name))
            msg()
            msg(f"{C['BRED']}Error: container "
                f"'{C['YELLOW']}{self._name}{C['BRED']}' is currently "
                f"locked{hint}.{C['RST']}")
            msg()
            sys.exit(1)
        return self

    def __exit__(self, *_) -> None:
        self.release()
