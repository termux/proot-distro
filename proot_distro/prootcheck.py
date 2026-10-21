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

# Architecture: A self-test of the proot binary, run before `login`,
# `run` or a build's RUN step hands it a guest. Everything else this
# program does to keep a guest inside its rootfs -- the pinned
# descriptor, the validated binds -- assumes proot translates the
# guest's paths at all, and a proot build exists that does not.
#
# proot 5.4.0, on any kernel from 4.8 on, switches a tracee to seccomp
# mode -- restarted with PTRACE_CONT, stopped only by its filter -- as
# soon as PTRACE_SETOPTIONS accepts PTRACE_O_TRACESECCOMP. It never
# checks that the child installed a filter, and the child ignores the
# result of doing so; a build without filter support compiles that
# step to "return 0". Debian's and Ubuntu's 5.4.0-3 (the proot of
# Ubuntu 26.04) is such a build. Under it only the first execve(2) is
# ever stopped: its loader opens the guest's path on the host, and
# every system call after that reaches the host unchanged. A session
# ran the host's /bin/sh, read the host's /etc and wrote wherever the
# invoking user can, and a Dockerfile's RUN did the same. 5.1.0 marked
# a tracee seccomp-driven only on a real seccomp stop, so the same
# build stayed on PTRACE_SYSCALL and translated everything.
#
# The test asks the question directly instead of matching versions:
# proot is run on the host's own root with one --bind, and a shell
# checks for a file that exists only through that bind. Exit status
# _CONFINED means the bind was there, _LEAKED that the shell saw the
# host as it is. The shell is the host's own, so nothing an image
# supplied is run, and the bind's both ends are in a fresh private
# directory. It costs a few milliseconds and runs once per process for
# a given binary and toggle.
#
# When proot leaks, the test is repeated with PROOT_NO_SECCOMP=1, which
# keeps proot on PTRACE_SYSCALL; if that confines, the session gets the
# variable. A build without filter support loses nothing by it. When
# neither confines, the caller refuses to start proot. When the test
# itself cannot tell -- no shell, proot failing to start, a timeout --
# the session gets PROOT_NO_SECCOMP=1 as well, since that toggle only
# ever makes proot stop on more system calls, never on fewer, and proot
# reports its own start-up failure better than a guess here could.

import os
import shutil
import subprocess
import tempfile

from proot_distro.constants import IS_TERMUX, TERMUX_PREFIX
from proot_distro.message import is_quiet, quote_path, warn

# What the probe's shell exits with. Neither is a status proot uses for
# its own failures (1 for a fatal error, 182 from its loader).
_CONFINED = 10
_LEAKED = 11
_SCRIPT = f'if test -e "$1"; then exit {_CONFINED}; fi; exit {_LEAKED}'

# The host's own shell: absolute, so neither PATH nor an image decides
# which binary the probe runs.
_PROBE_SHELL = f"{TERMUX_PREFIX}/bin/sh" if IS_TERMUX else "/bin/sh"

# Generous: proot plus a shell builtin takes milliseconds, but a phone
# under load is slow to start anything.
_PROBE_TIMEOUT = 30

_NO_SECCOMP = "PROOT_NO_SECCOMP"

# (proot_bin, toggle) -> the additions confining_env() settled on, so a
# build with many RUN steps probes once.
_verdicts = {}


class ProotConfinementError(Exception):
    """proot runs a guest without translating its paths."""


def _probe(proot_bin, toggles):
    """Run the self-test of *proot_bin* under the PROOT_* *toggles*.

    True when the guest saw the bind, False when it saw the host's own
    filesystem, and otherwise a short reason why neither could be told.
    """
    try:
        work = tempfile.mkdtemp(prefix="pd-proot-probe-")
    except OSError as exc:
        return f"self-test failed: {exc.strerror or exc}"
    try:
        src = os.path.join(work, "src")
        dst = os.path.join(work, "dst")
        os.mkdir(src, 0o700)
        os.mkdir(dst, 0o700)
        with open(os.path.join(src, "marker"), "x"):
            pass
        argv = [
            proot_bin, "--rootfs=/", f"--bind={src}:{dst}",
            _PROBE_SHELL, "-c", _SCRIPT, "probe",
            os.path.join(dst, "marker"),
        ]
        env = {"PATH": os.defpath}
        env.update(toggles)
        proc = subprocess.run(
            argv, env=env, cwd=work, stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=_PROBE_TIMEOUT,
        )
    except subprocess.TimeoutExpired:
        return f"self-test gave no answer within {_PROBE_TIMEOUT} seconds"
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        return f"self-test failed: {getattr(exc, 'strerror', None) or exc}"
    finally:
        shutil.rmtree(work, ignore_errors=True)
    if proc.returncode == _CONFINED:
        return True
    if proc.returncode == _LEAKED:
        return False
    return f"self-test exited with status {proc.returncode}"


def confining_env(proot_bin, env):
    """The PROOT_* variables *env* needs added for *proot_bin* to confine.

    *env* is the environment about to be handed to proot; only its
    PROOT_NO_SECCOMP is looked at, being the one toggle that decides
    whether proot stops on every system call. Returns a dict to merge
    into it (empty when nothing is needed), and raises
    ProotConfinementError when proot leaks in every mode it has.
    """
    toggles = {_NO_SECCOMP: env[_NO_SECCOMP]} if env.get(_NO_SECCOMP) else {}
    key = (proot_bin, toggles.get(_NO_SECCOMP))
    if key in _verdicts:
        return dict(_verdicts[key])

    shown = quote_path(proot_bin)
    first = _probe(proot_bin, toggles)
    if first is True:
        extra = {}
    elif first is False:
        if toggles or _probe(proot_bin, {_NO_SECCOMP: "1"}) is not True:
            raise ProotConfinementError(
                f"'{shown}' does not confine the programs it runs: its "
                f"self-test saw the host's files instead of a bind. "
                f"Install a working proot or point PD_PROOT_BIN at one."
            )
        extra = {_NO_SECCOMP: "1"}
        if not is_quiet():
            warn(f"'{shown}' skips path translation unless seccomp "
                 f"acceleration is off (a defect of some proot 5.4.0 "
                 f"builds); running it with {_NO_SECCOMP}=1. Export "
                 f"{_NO_SECCOMP}=1 to skip this warning.")
    else:
        extra = {} if toggles else {_NO_SECCOMP: "1"}
        if not is_quiet():
            suffix = "" if toggles else f"; running it with {_NO_SECCOMP}=1"
            warn(f"could not verify that '{shown}' confines the programs "
                 f"it runs ({first}){suffix}.")

    _verdicts[key] = extra
    return dict(extra)


__all__ = ("ProotConfinementError", "confining_env")
