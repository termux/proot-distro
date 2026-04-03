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

from proot_distro.constants import INSTALLED_ROOTFS_DIR, PD_CONFIGS_DIR, PROGRAM_NAME
from proot_distro.colors import C, msg


def command_list(args, configs: dict) -> None:
    detailed = getattr(args, "detailed", False)
    msg()
    if not configs:
        msg(f"{C['YELLOW']}No distribution configs found.{C['RST']}")
        msg()
        msg(f"{C['YELLOW']}Please check the directory '{PD_CONFIGS_DIR}' and add at least one distribution config.{C['RST']}")
    else:
        if detailed:
            msg(f"{C['CYAN']}Supported distributions:{C['RST']}")
        else:
            msg(f"{C['CYAN']}Supported distributions (format: name < alias >):{C['RST']}")
            msg()

        for alias in sorted(configs):
            cfg = configs[alias]
            installed = os.path.isdir(os.path.join(INSTALLED_ROOTFS_DIR, alias))
            if detailed:
                msg()
                msg(f"  {C['CYAN']}* {C['YELLOW']}{cfg.name}{C['RST']}")
                msg()
                msg(f"    {C['CYAN']}Alias:   {C['GREEN']}{alias}{C['RST']}")
                msg(f"    {C['CYAN']}Version: {C['GREEN']}{cfg.version}{C['RST']}")
                msg(f"    {C['CYAN']}Installed: {(C['GREEN'] + 'yes') if installed else (C['RED'] + 'no')}{C['RST']}")
                if cfg.description:
                    msg(f"    {C['CYAN']}Description: {cfg.description}{C['RST']}")
                archs = ", ".join(e.arch for e in cfg.architectures)
                msg(f"    {C['CYAN']}Architectures: {archs}{C['RST']}")
            else:
                msg(f"  {C['CYAN']}* {C['YELLOW']}{cfg.name} {C['GREEN']}< {alias} >{C['RST']}")

        msg()
        msg(f"{C['CYAN']}Install selected one with: {C['GREEN']}{PROGRAM_NAME} install <alias>{C['RST']}")
    msg()
