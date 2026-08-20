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

# Architecture: Registry of active proot sessions, surfaced by the `ps`
# command. A container may run several sessions at once (login/run take a
# shared container lock), so a single per-container record is not enough —
# one JSON file per session is written under SESSIONS_DIR, keyed by PID.
#
# Liveness is tracked with the same trick the container lock uses: each
# session holds an exclusive flock(2) on its own file via a file
# descriptor whose O_CLOEXEC bit is cleared, so proot — and every guest
# process it spawns — inherits the fd across os.execvpe(). The kernel
# releases the lock automatically when the last holder exits, even on a
# crash or `kill -9`, so liveness never depends on a cleanup hook or on
# os.kill(pid, 0) (which is fooled by PID recycling).
#
# `ps` reads each file and probes liveness with a *shared*, non-blocking
# flock: if the probe is refused the exclusive lock is still held (the
# session is alive); if it succeeds nobody holds the file (the session is
# dead) and the stale file is unlinked. A shared probe is used so two
# concurrent `ps` runs do not lock each other out and misreport a dead
# session as alive.
#
# Registration is strictly best-effort: any failure (including a
# filesystem without flock support) returns None and must never prevent
# a session from starting.
#
# SESSIONS_DIR is guest-writable: on Termux it sits under the
# $TERMUX_PREFIX bound read-write into every non-isolated container. It
# is therefore opened as a descriptor once, by an O_NOFOLLOW walk down
# from RUNTIME_DIR, and every entry below it is named as (dir_fd, name).
# os.makedirs(exist_ok=True) accepted a `sessions -> <host dir>` symlink
# and login then wrote its JSON there -- and, worse, active_sessions()
# unlinks every *.json whose flock probe says nobody holds it, so one
# `ps` emptied whichever host directory the link named of files ending
# in .json. The entries themselves are opened through open_regular_at,
# which refuses a symlink and a FIFO alike; an entry that is not a plain
# file is not one this module wrote, and pruning it removes the name
# only.

import fcntl
import json
import os
import stat
import time

from proot_distro import dirfd
from proot_distro.constants import RUNTIME_DIR, SESSIONS_DIR

# SESSIONS_DIR is RUNTIME_DIR/sessions; the walk below descends to it one
# component at a time rather than naming it.
_SESSIONS_PARTS = (os.path.basename(SESSIONS_DIR),)


def _sessions_dir_fd(create: bool = False):
    """Open SESSIONS_DIR. Descriptor, or None. The caller closes it.

    RUNTIME_DIR is the trust root, named the way every other module names
    it, and is created by name so a first `login` on a fresh machine
    still registers; `sessions` below it is walked with O_NOFOLLOW.
    """
    if create:
        try:
            os.makedirs(RUNTIME_DIR, exist_ok=True)
        except OSError:
            return None
    return dirfd.opendir_under(RUNTIME_DIR, _SESSIONS_PARTS, create=create)


def register_session(*, container, kind, command_argv, user,
                     isolated=False, minimal=False, detach=False):
    """Record the about-to-start session and return its locked fd, or None.

    Must be called from the process that immediately exec's into proot,
    right before os.execvpe(), so os.getpid() already equals the future
    proot PID. The returned fd must be kept referenced by the caller
    until execvpe() runs, otherwise it would be garbage-collected (and
    the lock released) before proot inherits it.

    Best-effort: every failure path returns None and is silently
    ignored so session tracking can never block a login/run.
    """
    dir_fd = _sessions_dir_fd(create=True)
    if dir_fd is None:
        return None
    try:
        return _register_at(
            dir_fd,
            container=container, kind=kind, command_argv=command_argv,
            user=user, isolated=isolated, minimal=minimal, detach=detach,
        )
    finally:
        os.close(dir_fd)


def _register_at(dir_fd, *, container, kind, command_argv, user,
                 isolated, minimal, detach):
    """register_session()'s body, with SESSIONS_DIR already validated."""
    pid = os.getpid()
    final_name = f"{pid}.json"
    tmp_name = f".{pid}.{os.urandom(4).hex()}.tmp"

    payload = {
        "pid": pid,
        "container": container,
        "kind": kind,
        "command": list(command_argv),
        "user": user,
        "start_time": time.time(),
        "isolated": bool(isolated),
        "minimal": bool(minimal),
        "detach": bool(detach),
    }

    try:
        raw, _st = dirfd.open_new_at(dir_fd, tmp_name)
    except OSError:
        return None
    try:
        fd = os.fdopen(raw, "w")
    except OSError:
        _safe_close_fd(raw)
        dirfd.unlink_quietly(dir_fd, tmp_name)
        return None

    # Clear O_CLOEXEC so the fd (and its flock) survives execvpe() and is
    # inherited by proot and its guest children.
    try:
        os.set_inheritable(fd.fileno(), True)
    except OSError:
        pass

    # Hold the file exclusively for the lifetime of the session. A fresh
    # temp file should never be contended; if flock is unsupported by the
    # filesystem we cannot track liveness robustly, so skip tracking.
    try:
        fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        _abandon(fd, dir_fd, tmp_name)
        return None

    try:
        json.dump(payload, fd)
        fd.write("\n")
        fd.flush()
    except OSError:
        _abandon(fd, dir_fd, tmp_name)
        return None

    # Atomic publish. flock lives on the open file description / inode,
    # not the path, so the rename preserves the lock while making the
    # complete record visible to readers in one step. Any stale file left
    # by a dead process that reused this PID is overwritten here — and a
    # symlink left under that name is replaced rather than written
    # through, rename(2) never following one at either end.
    try:
        os.replace(tmp_name, final_name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError:
        _abandon(fd, dir_fd, tmp_name)
        return None

    return fd


def active_sessions():
    """Return a list of live session records, pruning dead ones.

    Each record is the dict written by register_session(). Files whose
    holder has exited are unlinked as a side effect, so stale entries are
    never reported. Results are sorted by start time then PID.
    """
    dir_fd = _sessions_dir_fd()
    if dir_fd is None:
        return []
    try:
        try:
            names = dirfd.listdir_at(dir_fd)
        except OSError:
            return []

        sessions = []
        for name in names:
            if name.startswith(".") or not name.endswith(".json"):
                continue

            data = _read_record(dir_fd, name)

            if not _session_alive_at(dir_fd, name):
                dirfd.unlink_quietly(dir_fd, name)
                continue

            if isinstance(data, dict):
                sessions.append(data)
    finally:
        os.close(dir_fd)

    sessions.sort(key=lambda s: (s.get("start_time", 0.0), s.get("pid", 0)))
    return sessions


def _read_record(dir_fd, name):
    """Return the JSON dict in *name*, or None if it does not hold one."""
    try:
        fd, _st = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    except OSError:
        return None
    try:
        fh = os.fdopen(fd, "r", errors="replace")
    except OSError:
        _safe_close_fd(fd)
        return None
    with fh:
        try:
            return json.load(fh)
        except (OSError, ValueError):
            return None


def session_file(pid):
    """Path of the registry file of the session rooted at *pid*.

    register_session() names each file after the PID it exec's into, so
    this is derivable without re-reading the directory.
    """
    return os.path.join(SESSIONS_DIR, f"{pid}.json")


def session_is_live(pid):
    """Return True while any process of session *pid* is still running.

    Same flock probe active_sessions() uses, so a session counts as gone
    exactly when `ps` would stop listing it. Cheap enough (one open +
    one flock) to poll in a loop, unlike a /proc scan.
    """
    dir_fd = _sessions_dir_fd()
    if dir_fd is None:
        return False
    try:
        return _session_alive_at(dir_fd, f"{pid}.json")
    finally:
        os.close(dir_fd)


def session_holders(pid):
    """Return the PIDs that still hold session *pid*'s registry file open.

    register_session() clears O_CLOEXEC on that file's descriptor, so
    proot and every guest process it spawns inherit it. Looking for the
    inode in /proc therefore identifies the session's live members
    directly, which beats the recorded root PID on two counts: it cannot
    be fooled by PID reuse (a recycled PID does not hold the fd), and it
    still finds the guests after the root proot has been SIGKILLed and
    they were reparented to init — the case the recorded PID can no
    longer reach at all.

    Compares st_dev/st_ino rather than the readlink target because the
    descriptor is opened on the temporary path before os.replace()
    publishes it.

    Best-effort: an empty set means /proc was unreadable (or nothing
    holds the file), and the caller falls back to the recorded PID.
    """
    dir_fd = _sessions_dir_fd()
    if dir_fd is None:
        return set()
    try:
        st = dirfd.lstat_at(dir_fd, f"{pid}.json")
    except OSError:
        return set()
    finally:
        os.close(dir_fd)
    if not stat.S_ISREG(st.st_mode):
        # A symlink's own inode matches no descriptor anything holds, so
        # this is belt and braces — but the answer to "who holds a
        # planted entry open" is nobody, not "whoever holds its target".
        return set()
    wanted = (st.st_dev, st.st_ino)

    holders = set()
    self_pid = os.getpid()
    try:
        names = os.listdir("/proc")
    except OSError:
        return holders

    for name in names:
        if not name.isdigit():
            continue
        other = int(name)
        if other == self_pid:
            continue
        fd_dir = f"/proc/{name}/fd"
        try:
            fds = os.listdir(fd_dir)
        except OSError:
            continue  # process exited, or its fds are not ours to read
        for fd in fds:
            try:
                fst = os.stat(f"{fd_dir}/{fd}")
            except OSError:
                continue  # fd closed under us, or a dangling target
            if (fst.st_dev, fst.st_ino) == wanted:
                holders.add(other)
                break
    return holders


def _session_alive_at(dir_fd, name):
    """Return True iff a process still holds the exclusive lock on *name*.

    Probes with a shared, non-blocking flock: a refusal means the
    session's exclusive lock is still held (alive); success means the
    file is unheld (the session is dead). An entry that will not open as
    a plain file counts as dead, so the caller prunes it: nothing but
    register_session() writes here, and unlinking removes the name and
    nothing else.
    """
    try:
        fd, _st = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    except OSError:
        return False
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError:
            # EACCES/EAGAIN means the session's exclusive lock is held;
            # any other error is treated the same way so a still-running
            # session is never pruned by mistake.
            return True
        # Acquired the shared lock: nobody holds it, session is dead.
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _safe_close(fd):
    try:
        fd.close()
    except OSError:
        pass


def _safe_close_fd(fd):
    try:
        os.close(fd)
    except OSError:
        pass


def _abandon(fd, dir_fd, name):
    """Drop a half-written registration: close the handle, drop the file."""
    _safe_close(fd)
    dirfd.unlink_quietly(dir_fd, name)


__all__ = ("register_session", "active_sessions", "session_file",
           "session_is_live", "session_holders")
