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

# Architecture: Programmatic interface for driving proot-distro from
# Python without shelling out to the CLI. The public entry points are
# re-exported on the package root (proot_distro/__init__.py):
#
#   proot_distro.run(...)       — run a command inside a container,
#                                 capturing stdout/stderr and the exit code.
#   proot_distro.reinstall(...) — wipe a container's rootfs and rebuild it
#                                 from the image reference stored in its
#                                 manifest.json.
#
# Both reuse the same internals as the CLI — build_login_runtime for the
# proot argv + guest environment, command_install for the reinstall
# pipeline — so the two frontends can never drift. The only external
# binary ever spawned is proot itself, the same one the CLI exec's.

import json
import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

from proot_distro.message import log_info
from proot_distro.locking import ContainerLock
from proot_distro.names import require_valid_name
from proot_distro.paths import container_manifest, container_rootfs
from proot_distro.commands.install import command_install
from proot_distro.commands.login import build_login_runtime
from proot_distro.commands.remove import _remove_path


class ProotDistroError(Exception):
    """Base exception for the proot-distro Python interface."""


class ContainerNotInstalled(ProotDistroError):
    """Raised when an operation targets a container that is not installed."""


class CommandError(ProotDistroError):
    """Raised by run(check=True) when the guest command exits non-zero."""


class ProotResult:
    """Outcome of running a command inside a container.

    Attributes:
        returncode: exit status of the guest command (int).
        stdout:     captured standard output (bytes).
        stderr:     captured standard error (bytes).
    """

    __slots__ = ("returncode", "stdout", "stderr")

    def __init__(self, returncode: int, stdout: bytes, stderr: bytes) -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr

    @property
    def ok(self) -> bool:
        """True iff the guest command exited with status 0."""
        return self.returncode == 0

    def __repr__(self) -> str:
        return (
            f"ProotResult(returncode={self.returncode}, "
            f"stdout={self.stdout!r}, stderr={self.stderr!r})"
        )


def run(
    distribution: str,
    argv,
    *,
    user: str = "root",
    work_dir: str = "",
    env: "list | None" = None,
    bind: "list | None" = None,
    isolated: bool = False,
    minimal: bool = False,
    check: bool = False,
) -> ProotResult:
    """Run a command inside *distribution* and capture its output.

    ``argv`` must be a non-empty sequence of strings — the guest command
    and its arguments. A plain string is rejected to avoid surprising
    shell behaviour; pass ``["echo", "hello"]``, not ``"echo hello"``.

    Keyword options mirror the CLI's ``run`` flags:

      user      — user name, numeric UID, or 'user:group' (default root)
      work_dir  — working directory inside the guest (default: the
                  image's WorkingDir, else the user's home / '/')
      env       — extra VAR=VALUE strings added to the guest environment
      bind      — extra --bind entries (source[:destination])
      isolated  /  minimal  — run a stripped-down session
      check     — raise CommandError when the command exits non-zero

    Returns a :class:`ProotResult`. Raises :class:`ContainerNotInstalled`
    when the container does not exist, :class:`TypeError` when ``argv`` is
    a string, and :class:`ValueError` when ``argv`` is empty. Fatal CLI
    errors (unknown user, missing shell, unavailable emulator, ...)
    propagate as ``SystemExit``, matching the CLI's behaviour.
    """
    if isinstance(argv, str):
        raise TypeError(
            "argv must be a sequence of arguments, not a string. "
            "Pass e.g. ['echo', 'hello']."
        )
    argv = [str(a) for a in argv]
    if not argv:
        raise ValueError("argv must not be empty.")

    require_valid_name(distribution)

    if not os.path.isdir(container_rootfs(distribution)):
        raise ContainerNotInstalled(
            f"container '{distribution}' is not installed."
        )

    args = SimpleNamespace(
        container_name=distribution,
        user=user or "root",
        kernel=None,
        hostname=None,
        work_dir=work_dir or "",
        redirect_ports=False,
        isolated=isolated,
        minimal=minimal,
        shared_home=False,
        shared_tmp=False,
        shared_x11=False,
        no_link2symlink=False,
        no_sysvipc=False,
        no_kill_on_exit=False,
        bind=list(bind or []),
        env=list(env or []),
        login_cmd=[],
        _run_inner=argv,
        emulator=None,
    )

    # A shared container lock is held for the duration of the run so
    # install/remove/reset cannot race the guest. Unlike the CLI there is
    # no exec: subprocess.run blocks, the parent keeps the fd, and the
    # lock is released when this context exits.
    with ContainerLock(distribution, exclusive=False, command="run"):
        rt = build_login_runtime(distribution, args)
        try:
            proc = subprocess.run(
                rt["proot_args"],
                env=rt["child_env"],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                check=False,
            )
        except FileNotFoundError:
            raise ProotDistroError(
                f"proot executable '{rt['proot_bin']}' was not found; "
                "install it or point PD_PROOT_BIN at a working proot."
            ) from None

    result = ProotResult(proc.returncode, proc.stdout, proc.stderr)
    if check and not result.ok:
        detail = proc.stderr.decode(errors="replace").rstrip()
        raise CommandError(
            f"command {argv!r} exited with status {proc.returncode}"
            + (f": {detail}" if detail else "")
        )
    return result


def reinstall(distribution: str, *, allow_insecure: bool = False) -> None:
    """Wipe the rootfs of *distribution* and rebuild it from its manifest.

    The container's image reference and architecture are read from
    ``containers/<name>/manifest.json`` (written by the original
    install), so the reinstall keeps the same image and container name.
    The manifest itself is preserved. Returns None on success.

    Raises :class:`ContainerNotInstalled` when the container does not
    exist and :class:`ProotDistroError` when its manifest cannot be used
    for a reinstall. Fatal install errors (network failure, invalid
    image, ...) propagate as ``SystemExit``, matching the CLI's ``reset``.
    """
    require_valid_name(distribution)

    rootfs_dir = container_rootfs(distribution)
    if not os.path.isdir(rootfs_dir):
        raise ContainerNotInstalled(
            f"container '{distribution}' is not installed."
        )

    image_ref = None
    override_arch = None
    try:
        with open(container_manifest(distribution)) as fh:
            manifest_data = json.load(fh)
        image_ref = manifest_data.get("image_ref")
        override_arch = manifest_data.get("arch")
    except FileNotFoundError:
        pass
    except (OSError, json.JSONDecodeError) as exc:
        raise ProotDistroError(
            f"cannot read manifest for '{distribution}': {exc}"
        ) from exc

    if not image_ref:
        raise ProotDistroError(
            f"container '{distribution}' has no OCI manifest. "
            "Reinstall is supported for OCI images only."
        )

    with ContainerLock(distribution, exclusive=True, command="reinstall"):
        log_info(f"Removing rootfs of '{distribution}'...")
        if not _remove_path(rootfs_dir):
            shutil.rmtree(rootfs_dir, ignore_errors=True)

        command_install(
            SimpleNamespace(
                image_ref=image_ref,
                custom_container_name=distribution,
                override_arch=override_arch,
                allow_insecure=allow_insecure,
            )
        )


__all__ = (
    "ProotDistroError",
    "ContainerNotInstalled",
    "CommandError",
    "ProotResult",
    "run",
    "reinstall",
)
