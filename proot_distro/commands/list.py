"""
Proot-Distro - manage proot containers on Termux.

Created by Sylirre <sylirre@termux.dev> for Termux project.
Development assisted by Claude Code (https://claude.ai/code).

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""
import os

from proot_distro.constants import INSTALLED_ROOTFS_DIR, PROGRAM_NAME
from proot_distro.colors import C, msg


def command_list(args, configs: dict) -> None:  # noqa: ARG001
    msg()
    try:
        entries = sorted(
            e for e in os.listdir(INSTALLED_ROOTFS_DIR)
            if os.path.isdir(os.path.join(INSTALLED_ROOTFS_DIR, e))
        )
    except OSError:
        entries = []

    if not entries:
        msg(f"{C['YELLOW']}No distributions are installed.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Install one with: {C['GREEN']}{PROGRAM_NAME} install ubuntu:24.04{C['RST']}")
    else:
        msg(f"{C['CYAN']}Installed distributions:{C['RST']}")
        msg()
        for alias in entries:
            msg(f"  {C['CYAN']}* {C['GREEN']}{alias}{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Log in with: {C['GREEN']}{PROGRAM_NAME} login <alias>{C['RST']}")
    msg()
