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

# Architecture: CPU architecture detection and emulator selection. Keeps all
# machine-specific knowledge (ELF header parsing, QEMU binary names, 32-bit
# support probing) in one place so the rest of the codebase stays
# arch-agnostic. detect_installed_arch() accepts a rootfs path so callers
# are not coupled to the container storage layout.

import ctypes
import os
import shutil
import struct
import sys

from proot_distro.message import crit_error
from proot_distro.constants import TERMUX_PREFIX
from proot_distro.paths import container_rootfs


def get_device_cpu_arch() -> str:
    """Return the host CPU arch in proot-distro's naming scheme.

    armv7l / armv8l are collapsed to "arm"; everything else is the
    raw `uname -m` value.
    """
    machine = os.uname().machine
    if machine in ("armv7l", "armv8l"):
        return "arm"
    return machine


def supports_32bit() -> bool:
    """Return True if the host CPU supports 32-bit userspace execution."""
    machine = os.uname().machine

    if machine in ("x86_64", "amd64"):
        # All x86-64 CPUs support 32-bit x86.
        return True

    if machine in ("aarch64", "arm64"):
        # Mirror lscpu's technique: call personality(PER_LINUX32) and check
        # whether the kernel accepts it (only when CPU implements AArch32 EL0).
        # On modern 64-bit-only ARM cores AArch32 EL0 is missing and the
        # syscall returns -1; if libc cannot even be loaded we conservatively
        # assume the same (no 32-bit support) rather than falling through to
        # the catch-all `return True`, which would mislead the caller into
        # running an arm binary natively on a host that can't actually
        # execute it.
        PER_LINUX32 = 0x0008
        try:
            libc = ctypes.CDLL(None)
            prev = libc.personality(PER_LINUX32)
            if prev == -1:
                return False
            libc.personality(prev)  # restore
            return True
        except Exception:
            return False

    return True


_ELF_MACHINE_MAP = {
    3:   "i686",     # EM_386
    40:  "arm",      # EM_ARM
    62:  "x86_64",   # EM_X86_64
    183: "aarch64",  # EM_AARCH64
    243: "riscv64",  # EM_RISCV
}


def _elf_arch(path: str) -> str:
    """Return the proot-distro arch name for an ELF binary, or '' on failure."""
    try:
        with open(path, "rb") as fh:
            ident = fh.read(20)
        if len(ident) < 20 or ident[:4] != b"\x7fELF":
            return ""
        fmt = "<H" if ident[5] == 1 else ">H"  # EI_DATA: 1=LE, 2=BE
        e_machine = struct.unpack_from(fmt, ident, 18)[0]
        return _ELF_MACHINE_MAP.get(e_machine, "")
    except OSError:
        return ""


def detect_installed_arch(container_name_or_rootfs: str) -> str:
    """Detect CPU architecture of an installed container by reading ELF headers.

    Accepts either a plain container name (resolved as
    ``CONTAINERS_DIR/<name>/rootfs``) or a full path to the rootfs directory.
    """
    if os.sep in container_name_or_rootfs or container_name_or_rootfs.startswith("/"):
        root = container_name_or_rootfs
    else:
        root = container_rootfs(container_name_or_rootfs)

    candidates = [
        "/usr/bin/bash", "/usr/bin/sh", "/usr/bin/su", "/usr/bin/busybox",
        f"{TERMUX_PREFIX}/bin/bash",
        "/bin/bash", "/bin/sh", "/bin/su", "/bin/busybox",
    ]
    for rel in candidates:
        arch = _elf_arch(root + rel)
        if arch:
            return arch
    return "unknown"


# ---------------------------------------------------------------------------
# QEMU / CPU emulator helpers
# ---------------------------------------------------------------------------

_KNOWN_ARCHS = {"aarch64", "arm", "i686", "riscv64", "x86_64"}

# Docker platform strings and alternative names → proot-distro arch.
# Entries are matched after stripping a leading "linux/" prefix.
_DOCKER_TO_PROOT = {
    "arm64":   "aarch64",
    "arm/v7":  "arm",
    "arm":     "arm",
    "386":     "i686",
    "amd64":   "x86_64",
    "riscv64": "riscv64",
}


def normalize_arch(arch: str):
    """Return a canonical proot-distro arch name, or None if unrecognised.

    Accepts native names (``aarch64``, ``x86_64`` …), bare Docker names
    (``arm64``, ``amd64`` …), and ``linux/``-prefixed Docker platform
    strings (``linux/arm64``, ``linux/amd64`` …).
    """
    s = arch.strip()
    if s.startswith("linux/"):
        s = s[6:]
    if s in _KNOWN_ARCHS:
        return s
    return _DOCKER_TO_PROOT.get(s)


# Machine string reported by `uname -m` for each proot-distro arch.
# Used to assemble proot's --kernel-release tuple so emulated containers
# return the right name from uname(2).
ARCH_UNAME_M = {
    "aarch64": "aarch64",
    "arm":     "armv7l",
    "i686":    "i686",
    "x86_64":  "x86_64",
    "riscv64": "riscv64",
}


_QEMU_BIN_NAMES = {
    "aarch64": "qemu-aarch64",
    "arm":     "qemu-arm",
    "i686":    "qemu-i386",
    "riscv64": "qemu-riscv64",
    "x86_64":  "qemu-x86_64",
}

_QEMU_PKGS = {
    "aarch64": "qemu-user-aarch64",
    "arm":     "qemu-user-arm",
    "i686":    "qemu-user-i386",
    "riscv64": "qemu-user-riscv64",
    "x86_64":  "qemu-user-x86-64",
}


def get_emulator_args(
    dist_arch: str, device_arch: str, emulator_override: str = ""
) -> list:
    """Return proot -q <emulator> args, or [] if no emulation is needed.

    When emulator_override is given it is used as the emulator path directly,
    bypassing default selection and native-run checks.
    """
    if emulator_override:
        emu_path = emulator_override
        if not os.path.isfile(emu_path) or not os.access(emu_path, os.X_OK):
            crit_error(f"emulator '{emu_path}' is not found or not executable.")
            sys.exit(1)
    else:
        if dist_arch == device_arch:
            return []

        # 64-bit host can run 32-bit guest natively.
        if dist_arch == "arm" and device_arch == "aarch64" and supports_32bit():
            return []
        if dist_arch == "i686" and device_arch == "x86_64":
            return []

        bin_name = _QEMU_BIN_NAMES.get(dist_arch, "")
        if not bin_name:
            crit_error(f"unsupported architecture '{dist_arch}'. "
                       f"Valid values are: aarch64, arm, i686, riscv64, "
                       f"x86_64.")
            sys.exit(1)

        emu_path = shutil.which(bin_name) or ""
        if not emu_path:
            pkg = _QEMU_PKGS.get(dist_arch, f"qemu-user-{dist_arch}")
            crit_error(f"selected container requires emulator package "
                       f"'{pkg}' which is not installed.")
            sys.exit(1)

    args = ["-q", emu_path]
    # Extra bindings needed for QEMU to locate Android system libraries.
    for path in (
        "/apex", "/linkerconfig/ld.config.txt",
        f"{TERMUX_PREFIX}", "/system", "/vendor",
        "/plat_property_contexts", "/property_contexts",
    ):
        if os.path.exists(path):
            args += [f"--bind={path}"]
    return args
