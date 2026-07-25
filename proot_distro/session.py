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

import fcntl
import json
import os
import time

from proot_distro.constants import SESSIONS_DIR


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
    try:
        os.makedirs(SESSIONS_DIR, exist_ok=True)
    except OSError:
        return None

    pid = os.getpid()
    final_path = os.path.join(SESSIONS_DIR, f"{pid}.json")
    tmp_path = os.path.join(SESSIONS_DIR, f".{pid}.{os.urandom(4).hex()}.tmp")

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
        fd = open(tmp_path, "w")
    except OSError:
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
        _safe_close(fd)
        _safe_unlink(tmp_path)
        return None

    try:
        json.dump(payload, fd)
        fd.write("\n")
        fd.flush()
    except OSError:
        _safe_close(fd)
        _safe_unlink(tmp_path)
        return None

    # Atomic publish. flock lives on the open file description / inode,
    # not the path, so the rename preserves the lock while making the
    # complete record visible to readers in one step. Any stale file left
    # by a dead process that reused this PID is overwritten here.
    try:
        os.replace(tmp_path, final_path)
    except OSError:
        _safe_close(fd)
        _safe_unlink(tmp_path)
        return None

    return fd


def active_sessions():
    """Return a list of live session records, pruning dead ones.

    Each record is the dict written by register_session(). Files whose
    holder has exited are unlinked as a side effect, so stale entries are
    never reported. Results are sorted by start time then PID.
    """
    try:
        names = os.listdir(SESSIONS_DIR)
    except OSError:
        return []

    sessions = []
    for name in names:
        if name.startswith(".") or not name.endswith(".json"):
            continue
        path = os.path.join(SESSIONS_DIR, name)

        try:
            with open(path) as fh:
                data = json.load(fh)
        except (OSError, ValueError):
            data = None

        if not _session_alive(path):
            _safe_unlink(path)
            continue

        if isinstance(data, dict):
            sessions.append(data)

    sessions.sort(key=lambda s: (s.get("start_time", 0.0), s.get("pid", 0)))
    return sessions


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
    return _session_alive(session_file(pid))


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
    try:
        st = os.stat(session_file(pid))
    except OSError:
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


def _session_alive(path):
    """Return True iff a process still holds the exclusive lock on *path*.

    Probes with a shared, non-blocking flock: a refusal means the
    session's exclusive lock is still held (alive); success means the
    file is unheld (the session is dead).
    """
    try:
        fd = os.open(path, os.O_RDONLY)
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


def _safe_unlink(path):
    try:
        os.unlink(path)
    except OSError:
        pass


__all__ = ("register_session", "active_sessions", "session_file",
           "session_is_live", "session_holders")
