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

# Architecture: backgrounds a `login`/`run` session (--detach). The
# caller does all container setup in the foreground process so errors
# reach the user's terminal; only the final exec is daemonized here.
#
# Daemonization is a textbook double-fork:
#
#   foreground ─fork→ child ─setsid→ ─fork→ grandchild (the daemon)
#       │              │ _exit(0)              │ redirect 0/1/2 → /dev/null
#       │ waitpid      ▼ (reaped; grandchild   │ register_session()
#       │ read pid ◀────  reparented to init)  │ write pid to pipe
#       ▼ return pid                           └ execvpe(proot)
#
# setsid() puts the daemon in a new session with no controlling
# terminal, so closing the launching terminal won't SIGHUP it; the
# second fork makes the daemon a non-session-leader so it can never
# reacquire a controlling terminal. register_session() runs in the
# grandchild, so os.getpid() already equals the future proot PID and
# the `ps` entry/filename are correct. The grandchild inherits the
# foreground's (O_CLOEXEC-cleared) container-lock fd; the caller must
# disown() that lock so its own release() does not LOCK_UN it out from
# under the daemon.

import os

from proot_distro.session import register_session


def spawn_detached(proot_bin, proot_args, child_env, *, register_kwargs):
    """Launch proot as a detached daemon; return its PID, or None.

    Called from the foreground process in place of the usual
    register_session()+os.execvpe() tail. Returns the daemon's PID to
    the foreground (None if the daemon could not be started). The
    foreground never returns from inside the forked children.
    """
    try:
        read_fd, write_fd = os.pipe()
    except OSError:
        return None

    try:
        pid1 = os.fork()
    except OSError:
        _safe_close(read_fd)
        _safe_close(write_fd)
        return None

    if pid1 > 0:
        # Foreground: read the daemon PID the grandchild reports, reap
        # the intermediate child, and hand the PID back to the caller.
        _safe_close(write_fd)
        raw = b""
        try:
            while True:
                chunk = os.read(read_fd, 64)
                if not chunk:
                    break
                raw += chunk
        except OSError:
            pass
        _safe_close(read_fd)
        try:
            os.waitpid(pid1, 0)
        except OSError:
            pass
        try:
            return int(raw.decode().strip())
        except (UnicodeDecodeError, ValueError):
            return None

    # Intermediate child. Detach into a new session, then fork the
    # daemon and exit so the daemon is reparented to init and is not a
    # session leader.
    _safe_close(read_fd)
    try:
        os.setsid()
        pid2 = os.fork()
    except OSError:
        # Could not detach/fork the daemon: report failure by closing
        # the pipe with nothing written, then exit.
        _safe_close(write_fd)
        os._exit(1)

    if pid2 > 0:
        os._exit(0)

    # Grandchild — the daemon. From here any failure must end in
    # os._exit(); a traceback would be written to the already-detached
    # (and soon /dev/null) standard streams.
    try:
        _redirect_std_to_devnull()
        # Keep a reference until execvpe so the session fd (and its
        # inherited flock) is not closed early. Best-effort.
        _session_fd = register_session(**register_kwargs)  # noqa: F841
        try:
            os.write(write_fd, str(os.getpid()).encode())
        except OSError:
            pass
        _safe_close(write_fd)
        os.execvpe(proot_bin, proot_args, child_env)
    except BaseException:
        pass
    os._exit(127)


def _redirect_std_to_devnull() -> None:
    """Point stdin, stdout and stderr at /dev/null for the daemon."""
    devnull = os.open(os.devnull, os.O_RDWR)
    for target in (0, 1, 2):
        os.dup2(devnull, target)
    if devnull > 2:
        os.close(devnull)


def _safe_close(fd) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


__all__ = ("spawn_detached",)
