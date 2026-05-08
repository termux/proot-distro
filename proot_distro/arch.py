#
# Proot-Distro - manage proot containers on Termux.
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
import struct
import sys

from proot_distro.constants import PREFIX, CONTAINERS_DIR
from proot_distro.colors import C, msg


def get_device_cpu_arch() -> str:
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
        PER_LINUX32 = 0x0008
        try:
            libc = ctypes.CDLL(None)
            prev = libc.personality(PER_LINUX32)
            if prev == -1:
                return False
            libc.personality(prev)  # restore
            return True
        except Exception:
            pass

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


def detect_installed_arch(dist_name_or_rootfs: str) -> str:
    """Detect CPU architecture of an installed container by reading ELF headers.

    Accepts either a plain container name (resolved as
    ``CONTAINERS_DIR/<name>/rootfs``) or a full path to the rootfs directory.
    """
    if os.sep in dist_name_or_rootfs or dist_name_or_rootfs.startswith("/"):
        root = dist_name_or_rootfs
    else:
        root = os.path.join(CONTAINERS_DIR, dist_name_or_rootfs, "rootfs")

    candidates = [
        "/usr/bin/bash", "/usr/bin/sh", "/usr/bin/su", "/usr/bin/busybox",
        "/data/data/com.termux/files/usr/bin/bash",
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

_QEMU_BINS = {
    "aarch64": f"{PREFIX}/bin/qemu-aarch64",
    "arm":     f"{PREFIX}/bin/qemu-arm",
    "i686":    f"{PREFIX}/bin/qemu-i386",
    "riscv64": f"{PREFIX}/bin/qemu-riscv64",
    "x86_64":  f"{PREFIX}/bin/qemu-x86_64",
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
            msg()
            msg(f"{C['BRED']}Error: emulator "
                f"'{C['YELLOW']}{emu_path}{C['BRED']}' is not found or "
                f"not executable.{C['RST']}")
            msg()
            sys.exit(1)
    else:
        if dist_arch == device_arch:
            return []

        # 64-bit host can run 32-bit guest natively.
        if dist_arch == "arm" and device_arch == "aarch64" and supports_32bit():
            return []
        if dist_arch == "i686" and device_arch == "x86_64":
            return []

        emu_path = _QEMU_BINS.get(dist_arch, "")
        if not emu_path:
            msg()
            msg(f"{C['BRED']}Error: unsupported architecture "
                f"'{C['YELLOW']}{dist_arch}{C['BRED']}'. "
                f"Valid values are: aarch64, arm, i686, riscv64, "
                f"x86_64.{C['RST']}")
            msg()
            sys.exit(1)

        if not os.path.isfile(emu_path) or not os.access(emu_path, os.X_OK):
            pkg = _QEMU_PKGS.get(dist_arch, f"qemu-user-{dist_arch}")
            msg()
            msg(f"{C['BRED']}Error: your distribution requires package "
                f"'{C['YELLOW']}{pkg}{C['BRED']}' which is not "
                f"installed.{C['RST']}")
            msg()
            sys.exit(1)

    args = ["-q", emu_path]
    # Extra bindings needed for QEMU to locate Android system libraries.
    for path in (
        "/apex", "/linkerconfig/ld.config.txt",
        f"{PREFIX}", "/system", "/vendor",
        "/plat_property_contexts", "/property_contexts",
    ):
        if os.path.exists(path):
            args += [f"--bind={path}"]
    return args
