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

# Architecture: Lists installed proot containers by scanning CONTAINERS_DIR
# for subdirectories that contain a rootfs/ entry. When none are found,
# prints an install suggestion.

import os

from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.colors import C, msg


def command_list(args, configs: dict) -> None:  # noqa: ARG001
    msg()
    try:
        entries = sorted(
            e for e in os.listdir(CONTAINERS_DIR)
            if os.path.isdir(os.path.join(CONTAINERS_DIR, e, "rootfs"))
        )
    except OSError:
        entries = []

    if not entries:
        msg(f"{C['YELLOW']}No containers are installed.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Install one with: "
            f"{C['GREEN']}{PROGRAM_NAME} install ubuntu:24.04{C['RST']}")
    else:
        msg(f"{C['CYAN']}Installed containers:{C['RST']}")
        msg()
        for name in entries:
            msg(f"  {C['CYAN']}* {C['GREEN']}{name}{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Log in with: "
            f"{C['GREEN']}{PROGRAM_NAME} login <name>{C['RST']}")
    msg()
