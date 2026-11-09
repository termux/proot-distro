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

# Architecture: process execution for prepared login/run sessions. Preparation
# remains in operations/session_prepare.py; foreground workers exec here and
# detached workers exec a fresh launcher before invoking the double-fork below.
#
# The extra exec matters when a CLI is embedded in a process with application
# threads. Calling os.fork() there can deadlock on a Python or libc lock that
# another thread held at the instant of the fork, and recent CPython versions
# warn about it. subprocess performs only its controlled exec setup in that
# vulnerable child. All project Python code after fork runs in the fresh,
# known-single-threaded _detach_worker interpreter instead. The shared private
# bootstrap disables site and environment-controlled Python startup before it
# imports the exact package this process is already running:
#
#   caller ─subprocess→ fresh launcher ─fork→ child ─setsid→ ─fork→ daemon
#      │                        │          │ _exit(0)              │ detach I/O
#      │ bounded JSON request   │ waitpid  ▼ (reparent daemon)     │ register
#      │ bounded JSON reply  ◀──┤ read pid/exec handshake         │ exec proot
#      ▼ return pid             ▼ exit                            ▼
#
# The control pipe is the daemon's only setup reply -- its standard streams
# are detached or routed to caller-selected log files by then -- and the write
# end is CLOEXEC, so a
# successful exec closes it and the foreground reads the PID alone. A
# refused exec (E2BIG from an environment too large in total, ENOMEM,
# a NUL in the argv) writes the reason after the PID instead, and the
# foreground reports that rather than a session that never started.
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

import json
import os
import subprocess

from proot_distro._bootstrap import private_module_command
from proot_distro.constants import BASE_CACHE_DIR, RUNTIME_DIR
from proot_distro.message import quote_error
from proot_distro.session import register_session
from proot_distro.session_errors import SessionExecutionFailure


_MAX_DETACH_REQUEST_BYTES = 16 * 1024 * 1024
_MAX_DETACH_RESPONSE_BYTES = 64 * 1024
_LAUNCHER_ENV_KEYS = (
    "ANDROID_DATA",
    "ANDROID_ROOT",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "LD_LIBRARY_PATH",
    "TERMUX_APP__APP_VERSION_NAME",
    "TERMUX_APP__PACKAGE_NAME",
    "TERMUX_VERSION",
    "TERMUX__HOME",
    "TERMUX__PREFIX",
)


def _launcher_environment():
    """Return only what the Python launcher and platform runtime require.

    The eventual Proot environment is carried in the bounded request and is
    intentionally independent. Forwarding the embedding application's whole
    environment both leaked unrelated values into an internal process and made
    this extra exec fail with E2BIG where the former direct-fork path worked.
    Private path overrides make the fresh interpreter use exactly the state
    roots already selected by this process.
    """
    env = {
        key: os.environ[key] for key in _LAUNCHER_ENV_KEYS
        if key in os.environ
    }
    env["_PROOT_DISTRO_API_RUNTIME_DIR"] = os.path.abspath(RUNTIME_DIR)
    env["_PROOT_DISTRO_API_CACHE_DIR"] = os.path.abspath(BASE_CACHE_DIR)
    env["PD_FORCE_NO_COLORS"] = "1"
    return env


def execute_session(prepared) -> None:
    """Register and replace this process with a prepared Proot session."""
    launch = prepared.launch
    _session_fd = register_session(  # noqa: F841 - retained through exec
        **launch.registration(False)
    )
    os.fchdir(prepared.rootfs_fd)
    try:
        os.execvpe(
            launch.executable, list(launch.argv), dict(launch.environment)
        )
    except (OSError, ValueError) as exc:
        raise SessionExecutionFailure(
            f"cannot execute proot: {quote_error(exc)}"
        ) from exc


def detach_session(prepared, *, stdout_fd=None, stderr_fd=None) -> int:
    """Start a prepared session as a daemon and transfer its shared lock."""
    launch = prepared.launch
    pid = spawn_detached(
        launch.executable,
        list(launch.argv),
        dict(launch.environment),
        register_kwargs=launch.registration(True),
        rootfs_fd=prepared.rootfs_fd,
        lock_fd=prepared.inherited_lock_fd(),
        stdout_fd=stdout_fd,
        stderr_fd=stderr_fd,
    )
    prepared.disown_lock()
    return pid


def spawn_detached(proot_bin, proot_args, child_env, *, register_kwargs,
                   rootfs_fd=None, lock_fd=None, stdout_fd=None,
                   stderr_fd=None):
    """Launch proot through a fresh daemonization worker and return its PID.

    The caller may have arbitrary application threads. It must not execute
    project Python code after fork, so a subprocess first execs
    :mod:`proot_distro._detach_worker`. That known-single-threaded interpreter
    performs the traditional double-fork in :func:`_spawn_detached_local`.

    Descriptor numbers are meaningful because ``pass_fds`` preserves them
    across the launcher exec. The rootfs pin and optional output descriptors
    are close-on-exec again inside the launcher; *lock_fd* deliberately remains
    inheritable so proot holds the container's shared lock for its lifetime.
    """
    request = {
        "proot_bin": proot_bin,
        "proot_args": proot_args,
        "child_env": child_env,
        "register_kwargs": register_kwargs,
        "rootfs_fd": rootfs_fd,
        "lock_fd": lock_fd,
        "stdout_fd": stdout_fd,
        "stderr_fd": stderr_fd,
    }
    try:
        raw_request = json.dumps(
            request, ensure_ascii=True, separators=(",", ":")
        ).encode("ascii")
    except (TypeError, ValueError, UnicodeError, RecursionError) as exc:
        raise SessionExecutionFailure(
            f"cannot encode detached-session request: {quote_error(exc)}"
        ) from exc
    if len(raw_request) > _MAX_DETACH_REQUEST_BYTES:
        raise SessionExecutionFailure(
            "detached-session request exceeds its size limit."
        )

    pass_fds = tuple(sorted({
        fd for fd in (rootfs_fd, lock_fd, stdout_fd, stderr_fd)
        if isinstance(fd, int) and not isinstance(fd, bool) and fd > 2
    }))
    env = _launcher_environment()
    try:
        launcher = subprocess.Popen(
            private_module_command("proot_distro._detach_worker"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=env,
            pass_fds=pass_fds,
        )
    except (OSError, ValueError) as exc:
        raise SessionExecutionFailure(
            f"cannot start detached-session launcher: {quote_error(exc)}"
        ) from exc

    try:
        stdout, _stderr = launcher.communicate(raw_request)
    except (OSError, ValueError) as exc:
        try:
            launcher.kill()
            launcher.wait()
        except (AttributeError, OSError):
            pass
        raise SessionExecutionFailure(
            f"detached-session launcher communication failed: "
            f"{quote_error(exc)}"
        ) from exc
    if len(stdout) > _MAX_DETACH_RESPONSE_BYTES:
        raise SessionExecutionFailure(
            "detached-session launcher returned an oversized response."
        )
    try:
        response = json.loads(stdout)
    except (ValueError, UnicodeDecodeError, RecursionError):
        response = None
    if not isinstance(response, dict):
        raise SessionExecutionFailure(
            "detached-session launcher returned an invalid response."
        )
    if set(response) not in ({"pid"}, {"error"}):
        raise SessionExecutionFailure(
            "detached-session launcher returned an invalid response."
        )
    error = response.get("error")
    if error is not None:
        if not isinstance(error, str) or not error:
            raise SessionExecutionFailure(
                "detached-session launcher returned an invalid error."
            )
        raise SessionExecutionFailure(error)
    pid = response.get("pid")
    if (launcher.returncode != 0 or isinstance(pid, bool)
            or not isinstance(pid, int) or pid <= 0):
        raise SessionExecutionFailure(
            "detached-session launcher did not report a valid PID."
        )
    return pid


def _spawn_detached_local(proot_bin, proot_args, child_env, *,
                          register_kwargs, rootfs_fd=None, stdout_fd=None,
                          stderr_fd=None):
    """Double-fork from the single-threaded launcher and return the daemon PID.

    Startup failures raise ``SessionExecutionFailure``. Forked children never
    return into their caller. This function is private because calling it from
    an arbitrary application process would restore the multithreaded-fork bug
    that :func:`spawn_detached` exists to avoid.

    *rootfs_fd* is the descriptor the container's rootfs was pinned as.
    The daemon chdirs into it before the exec, for the same reason the
    foreground executor does: the argv says
    `--rootfs=.` so proot resolves the guest root against an inode
    rather than a name. The chdir happens in the grandchild, so the
    foreground's own working directory is left alone.

    Standard input always becomes ``/dev/null``. *stdout_fd* and *stderr_fd*
    are optional append-only descriptors opened by the API worker; omitted
    outputs also become ``/dev/null``. Sources are closed after ``dup2``.
    """
    try:
        read_fd, write_fd = os.pipe()
    except OSError as exc:
        raise SessionExecutionFailure(
            f"cannot create detached-session control pipe: {quote_error(exc)}"
        ) from exc

    try:
        pid1 = os.fork()
    except OSError as exc:
        _safe_close(read_fd)
        _safe_close(write_fd)
        raise SessionExecutionFailure(
            f"cannot fork detached session: {quote_error(exc)}"
        ) from exc

    if pid1 > 0:
        # Foreground: read the daemon PID the grandchild reports, reap
        # the intermediate child, and hand the PID back to the caller.
        _safe_close(write_fd)
        raw = b""
        try:
            while True:
                chunk = os.read(read_fd, 4096)
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
        # The PID on the first line; a second line is the daemon saying
        # its exec was refused, which is not a session.
        pid_text, _, reason = raw.decode(errors="replace").partition("\n")
        if reason:
            raise SessionExecutionFailure(
                f"cannot execute proot: {reason.strip()}"
            )
        try:
            pid = int(pid_text.strip())
        except ValueError:
            raise SessionExecutionFailure(
                "detached session did not report a valid PID."
            ) from None
        if pid <= 0:
            raise SessionExecutionFailure(
                "detached session did not report a valid PID."
            )
        return pid

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
    # standard streams.
    try:
        _redirect_std(stdout_fd=stdout_fd, stderr_fd=stderr_fd)
        if rootfs_fd is not None:
            os.fchdir(rootfs_fd)
        # Keep a reference until execvpe so the session fd (and its
        # inherited flock) is not closed early. Best-effort.
        _session_fd = register_session(**register_kwargs)  # noqa: F841
        try:
            os.write(write_fd, f"{os.getpid()}\n".encode())
        except OSError:
            pass
        # The write end stays open across the exec on purpose: it is
        # CLOEXEC, so success closes it and the foreground sees EOF,
        # while a refusal is reported through it below. Closing it here
        # first was what let a refused exec pass for a started session.
        try:
            os.execvpe(proot_bin, proot_args, child_env)
        except (OSError, ValueError) as exc:
            reason = getattr(exc, "strerror", None) or str(exc)
            try:
                os.write(write_fd, reason.encode(errors="replace"))
            except OSError:
                pass
    except BaseException:
        pass
    _safe_close(write_fd)
    os._exit(127)


def _redirect_std(*, stdout_fd=None, stderr_fd=None) -> None:
    """Detach stdio, optionally retaining append-only output descriptors."""
    devnull = os.open(os.devnull, os.O_RDWR)
    sources = (
        devnull,
        devnull if stdout_fd is None else stdout_fd,
        devnull if stderr_fd is None else stderr_fd,
    )
    for target, source in enumerate(sources):
        os.dup2(source, target)
    for fd in set(sources):
        if fd > 2:
            os.close(fd)


def _safe_close(fd) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


__all__ = ("detach_session", "execute_session", "spawn_detached")
