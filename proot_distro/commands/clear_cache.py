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

# Architecture: Removes the entire download cache directory contents.
# Failures on individual entries are reported but do not abort the operation
# so partial caches are still cleaned as much as possible.

import os
import shutil
import stat

from proot_distro.constants import DOWNLOAD_CACHE_DIR
from proot_distro.colors import C, msg


def _ensure_readable(path: str) -> None:
    """Attempt to add read/execute permissions to a directory entry."""
    try:
        st = os.stat(path)
        if os.path.isdir(path):
            os.chmod(path, st.st_mode | stat.S_IRWXU)
        else:
            os.chmod(path, st.st_mode | stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


def command_clear_cache(args, configs: dict) -> None:  # noqa: ARG001
    verbose = getattr(args, "verbose", False)

    if not os.path.isdir(DOWNLOAD_CACHE_DIR):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Download cache is empty.{C['RST']}")
        return

    total = 0
    for dirpath, _dirs, filenames in os.walk(DOWNLOAD_CACHE_DIR):
        _ensure_readable(dirpath)
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            _ensure_readable(fpath)
            try:
                total += os.path.getsize(fpath)
            except OSError:
                pass

    if total == 0 and not any(True for _ in os.scandir(DOWNLOAD_CACHE_DIR)):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Download cache is empty.{C['RST']}")
        return

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Clearing download cache...{C['RST']}")

    for entry in os.scandir(DOWNLOAD_CACHE_DIR):
        try:
            if entry.is_dir(follow_symlinks=False):
                if verbose:
                    for dirpath, _dirs, filenames in os.walk(entry.path):
                        for fname in filenames:
                            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                                f"{C['CYAN']}Removing: "
                                f"'{os.path.join(dirpath, fname)}'{C['RST']}")
                shutil.rmtree(entry.path)
            else:
                if verbose:
                    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                        f"Removing: '{entry.path}'{C['RST']}")
                os.remove(entry.path)
        except OSError as exc:
            msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
                f"Failed to remove '{entry.path}': {exc}{C['RST']}")

    if total >= 1 << 20:
        total_str = f"{total / (1 << 20):.1f} MiB"
    elif total >= 1 << 10:
        total_str = f"{total / (1 << 10):.1f} KiB"
    else:
        total_str = f"{total} B"

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Reclaimed {total_str} of disk space.{C['RST']}")
