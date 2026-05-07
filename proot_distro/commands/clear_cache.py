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
import shutil

from proot_distro.constants import DOWNLOAD_CACHE_DIR
from proot_distro.colors import C, msg


def command_clear_cache(args, configs: dict) -> None:  # noqa: ARG001
    if not os.path.isdir(DOWNLOAD_CACHE_DIR):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Download cache is empty.{C['RST']}")
        return

    total = 0
    for dirpath, _dirs, filenames in os.walk(DOWNLOAD_CACHE_DIR):
        for fname in filenames:
            try:
                total += os.path.getsize(os.path.join(dirpath, fname))
            except OSError:
                pass

    if total == 0 and not any(True for _ in os.scandir(DOWNLOAD_CACHE_DIR)):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Download cache is empty.{C['RST']}")
        return

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Clearing download cache...{C['RST']}")

    for entry in os.scandir(DOWNLOAD_CACHE_DIR):
        try:
            if entry.is_dir(follow_symlinks=False):
                shutil.rmtree(entry.path)
            else:
                os.remove(entry.path)
        except OSError as exc:
            msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}Failed to remove '{entry.path}': {exc}{C['RST']}")

    if total >= 1 << 20:
        total_str = f"{total / (1 << 20):.1f} MiB"
    elif total >= 1 << 10:
        total_str = f"{total / (1 << 10):.1f} KiB"
    else:
        total_str = f"{total} B"

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Reclaimed {total_str} of disk space.{C['RST']}")
