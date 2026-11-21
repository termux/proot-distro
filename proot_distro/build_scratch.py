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

"""Build scratch roots, and the sweep of the ones dead builds left.

A build assembles every stage's rootfs in a scratch root of its own and
removes it when it ends (operations/build.py). A build process killed
outright never gets to, and a second interrupt stops that removal, so
the next build and `clear-cache` sweep the roots whose build is gone.

A root is made in RUNTIME_DIR/build-tmp, or in the system temporary
directory when the runtime tree cannot hold one, and is named

    pd-build-<pid>-<start>.<8 hex digits>

after the process that made it: its ID, and its start time in clock
ticks since boot, which tells it apart from a later process given the
same ID. The root holds an exclusive flock on itself for as long as its
build runs.

Builds of 5.10 named theirs pd-build-<pid>.<8 hex digits>, with no start
time and no lock, and so does a build that cannot read its own start
time. Those are judged by the ID alone. The roots 5.10 made in the system
temporary directory (pd-build- and eight random characters) carry no
process ID at all, so nothing can tell a dead build's from a running
one's, and they are never swept.

The naming, the lock and the sweep are not the build's alone:
make_root_in() and reclaim_roots() take the prefix and the pattern, and
container_staging.py builds new containers in roots made the same way.
"""

import errno
import fcntl
import os
import re
import tempfile

from proot_distro import dirfd, statedir
from proot_distro.constants import RUNTIME_DIR

BUILD_TMP = os.path.join(RUNTIME_DIR, "build-tmp")

# The names this module gives, and the ones 5.10 gave in build-tmp.
# Nothing else in either directory is the sweep's to touch.
_SCRATCH_NAME = re.compile(
    r"pd-build-([0-9]{1,10})(?:-([0-9]{1,20}))?\.[0-9a-f]{8}"
)

# mkdir under a fresh name until one is free. A name holds 32 random
# bits, so a clash means a planted entry, and few are worth retrying.
_NAME_ATTEMPTS = 16


def _process_start(pid: int):
    """Start time of process *pid* in clock ticks since boot, or None.

    Field 22 of /proc/<pid>/stat. The command name before it is in
    parentheses and may itself hold spaces and parentheses, so fields
    are counted from the last ')'. None when it cannot be read: no such
    process, a /proc that hides it, or no /proc at all.
    """
    try:
        with open(f"/proc/{pid}/stat", "rb") as handle:
            data = handle.read()
    except (OSError, ValueError):
        return None
    end = data.rfind(b")")
    if end < 0:
        return None
    fields = data[end + 1:].split()
    try:
        return int(fields[19])
    except (IndexError, ValueError):
        return None


def _scratch_name(prefix: str = "pd-build") -> str:
    """A fresh root name for this process (see the module docstring)."""
    pid = os.getpid()
    start = _process_start(pid)
    stamp = "" if start is None else f"-{start}"
    return f"{prefix}-{pid}{stamp}.{os.urandom(4).hex()}"


def _lock_scratch(fd: int) -> bool:
    """Take the lock that marks a scratch root in use. False if held.

    flock(2) on the directory itself, exclusive and non-blocking. The
    kernel drops it with the last descriptor of that open, so a build
    process killed outright lets go of its root too. A filesystem that
    cannot hold the lock answers as if it were free, the rule
    locking.py applies; the process check in _maker_is_gone() is what
    keeps a running build's root then.
    """
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        return exc.errno not in (errno.EAGAIN, errno.EACCES)
    return True


def make_root_in(dir_fd: int, prefix: str = "pd-build", mode: int = 0o700):
    """Create, open and lock a fresh root under dir_fd. (name, fd).

    Named <prefix>-<pid>-<start>.<8 hex digits> (see the module
    docstring) and made with *mode*, which the umask narrows as it does
    for any mkdir. The caller owns the descriptor, which holds the lock.
    """
    for _attempt in range(_NAME_ATTEMPTS):
        name = _scratch_name(prefix)
        try:
            os.mkdir(name, mode, dir_fd=dir_fd)
        except FileExistsError:
            continue
        # Off the directory's descriptor, refusing a link, so what comes
        # back names the directory just created and not whatever the
        # name leads to by the time anything under it is used.
        fd = dirfd.opendir_at(dir_fd, name)
        # Between the mkdir and this, the root is unlocked; the process
        # in its name, alive and the same, keeps a sweep off it then.
        _lock_scratch(fd)
        return name, fd
    raise FileExistsError(errno.EEXIST, "no free scratch root name")


def make_scratch_root():
    """Create the scratch root a build assembles its stages in. (path, fd).

    RUNTIME_DIR/build-tmp is a predictable name inside the state tree,
    and tempfile.mkdtemp(dir=...) resolved it: a guest that left
    `build-tmp -> <host dir>` behind -- the runtime tree is under the
    $TERMUX_PREFIX bound read-write into every non-isolated container --
    had every stage rootfs, every spooled ADD and every packed layer
    assembled inside that host directory, and the cleanup at the end of
    the build removed what it found there. The directory is walked down
    to with O_NOFOLLOW and the root created with mkdirat off the
    descriptor that walk validated; what is made *inside* it is this
    process's own, since the name is fresh and the mode is 0700.

    A runtime tree that cannot hold one falls back to the system
    temporary directory, which is what the old `build_tmp = None` did.
    The root is made and named there the same way, so the sweep can
    tell whose it is.

    The descriptor comes back with the path because the *contents* of
    the root are named too: each stage's directory and rootfs, the ADD
    spool, a COPY --from image's throwaway tree. Composing those onto
    the path and opening them left every one of them re-resolvable by
    anything running as the invoking user -- which is what a RUN step's
    leftovers are. The caller owns the descriptor, which also holds the
    root's lock, and closes it once the root is removed.
    """
    try:
        dir_fd = statedir.open_state_dir(BUILD_TMP, create=True)
    except OSError:
        return _make_fallback_root()
    try:
        name, fd = make_root_in(dir_fd)
    except OSError:
        return _make_fallback_root()
    finally:
        os.close(dir_fd)
    return os.path.join(BUILD_TMP, name), fd


def _make_fallback_root():
    """The scratch root in the system temporary directory.

    The directory is opened as the system names it -- a /tmp that is a
    link is still the system's -- and the root under it refusing a link.
    In a sticky /tmp no other user can replace the root once made.
    """
    parent = tempfile.gettempdir()
    dir_fd = dirfd.opendir(parent)
    try:
        name, fd = make_root_in(dir_fd)
    finally:
        os.close(dir_fd)
    return os.path.join(parent, name), fd


def _maker_is_gone(pid: int, start) -> bool:
    """True when the process that made a root is known to have ended.

    Gone when no process has its ID. Gone when the process that has it
    belongs to another user (EPERM): the sweep only touches roots this
    user owns, a root is owned by the user its maker ran as, and this
    user may signal any process running as itself. Gone when the name
    carries a start time and the process holding the ID now started at
    another one. Without that last check a reused ID kept a dead build's
    root -- every stage's rootfs -- until the unrelated process holding
    it exited, and on Android process IDs come round again quickly.

    Whatever cannot be asked about counts as alive: when in doubt, a
    root is kept.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return True
    except PermissionError as exc:
        return exc.errno == errno.EPERM
    except (OSError, OverflowError, ValueError):
        return False
    if start is None:
        return False
    current = _process_start(pid)
    return current is not None and current != start


def _open_build_tmp():
    # ValueError: build-tmp outside the state roots, which a caller that
    # narrows them (tests do) can arrange. Nothing to sweep there then.
    try:
        return statedir.open_state_dir(BUILD_TMP)
    except (OSError, ValueError):
        return None


def _open_system_temp():
    try:
        return dirfd.opendir(tempfile.gettempdir())
    except OSError:
        return None


def _claim(dir_fd: int, pattern, claimed: list) -> None:
    """Add the left-behind roots under dir_fd to *claimed*, locked.

    *pattern* matches the names to consider, with the maker's process ID
    as its first group and its start time, if any, as the second.
    """
    try:
        names = os.listdir(dir_fd)
    except OSError:
        return
    euid = os.geteuid()
    for name in sorted(names):
        match = pattern.fullmatch(name)
        if match is None:
            continue
        start = None if match.group(2) is None else int(match.group(2))
        if not _maker_is_gone(int(match.group(1)), start):
            continue
        try:
            fd = dirfd.opendir_at(dir_fd, name)
        except OSError:
            continue
        try:
            owned = os.fstat(fd).st_uid == euid
        except OSError:
            owned = False
        if not owned or not _lock_scratch(fd):
            os.close(fd)
            continue
        claimed.append((dir_fd, name, fd))


def reclaim_scratch_roots(announce=None) -> int:
    """Remove the scratch roots of builds that are no longer running.

    Both places a root is made are swept: build-tmp, and the system
    temporary directory a build falls back to (reclaim_roots).
    """
    return reclaim_roots(
        (_open_build_tmp, _open_system_temp), _SCRATCH_NAME, announce
    )


def reclaim_roots(openers, pattern, announce=None) -> int:
    """Remove the roots, under each directory *openers* open, left behind.

    Each opener returns a directory descriptor, or None when there is
    nothing to sweep there. A root is taken for left behind only when
    its name matches *pattern* (see _claim), this user owns it, the
    process that made it is gone (_maker_is_gone), and its lock is free.
    The process check covers the moment before the lock is taken, 5.10
    builds that took none, and a filesystem that cannot hold one; the
    lock covers a process that cannot be seen from here and a root
    renamed after the fact. The owner check keeps the sweep off other
    users' roots in a shared /tmp. The lock is kept while the root is
    removed, so two sweeps never work on one.

    Entries are opened O_NOFOLLOW and removed by rmtree_at(), which
    unlinks a link instead of following it. build-tmp is guest-writable
    on Termux, and so is the temporary directory under $PREFIX: what is
    planted there is removed at most, never reached through.

    *announce(count)* is called before anything is removed, which can
    take a while. Returns how many roots were removed; a failure to look
    or to remove is never the caller's.
    """
    parents = []
    claimed = []
    try:
        for opener in openers:
            dir_fd = opener()
            if dir_fd is not None:
                parents.append(dir_fd)
                _claim(dir_fd, pattern, claimed)
        if not claimed:
            return 0
        if announce is not None:
            announce(len(claimed))
        for dir_fd, name, _fd in claimed:
            dirfd.rmtree_at(
                dir_fd, name, force=True, on_error=lambda _rel, _exc: None
            )
        return len(claimed)
    finally:
        for _dir_fd, _name, fd in claimed:
            os.close(fd)
        for dir_fd in parents:
            os.close(dir_fd)


__all__ = (
    "BUILD_TMP",
    "make_root_in",
    "make_scratch_root",
    "reclaim_roots",
    "reclaim_scratch_roots",
)
