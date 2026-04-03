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
import sys

from proot_distro.constants import DOWNLOAD_CACHE_DIR
from proot_distro.colors import C, msg


def command_clear_cache(args, configs: dict) -> None:
    if not os.path.isdir(DOWNLOAD_CACHE_DIR):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Download cache is empty.{C['RST']}")
        return

    cached_files = [
        os.path.join(DOWNLOAD_CACHE_DIR, f)
        for f in os.listdir(DOWNLOAD_CACHE_DIR)
        if os.path.isfile(os.path.join(DOWNLOAD_CACHE_DIR, f))
    ]

    if not cached_files:
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Download cache is empty.{C['RST']}")
        return

    total = sum(os.path.getsize(f) for f in cached_files)
    total_str = f"{total // (1024*1024)} MiB" if total >= 1024*1024 else f"{total // 1024} KiB"

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Clearing cache files...{C['RST']}")
    for fpath in cached_files:
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Deleting '{fpath}'{C['RST']}")
        os.remove(fpath)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Reclaimed {total_str} of disk space.{C['RST']}")
