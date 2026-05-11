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

# Architecture: Removes an entire container directory (rootfs + manifest)
# recursively, fixing permissions on the fly to handle subtrees that were
# chmod-000'd inside the container.

import os
import stat
import sys

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.colors import C, msg


def _remove_path(path: str, on_remove=None) -> bool:
    """Remove path recursively, fixing permissions on the fly.

    Returns True on full success. Any failure returns False and the partial
    state is left on disk. The optional on_remove callback is called with the
    path of each successfully removed entry.
    """
    try:
        st = os.lstat(path)
    except OSError:
        return True

    if not stat.S_ISDIR(st.st_mode):
        if not stat.S_ISLNK(st.st_mode):
            needed = stat.S_IRUSR | stat.S_IWUSR
            if (st.st_mode & needed) != needed:
                try:
                    os.chmod(path, st.st_mode | needed)
                except OSError:
                    pass
        try:
            os.unlink(path)
            if on_remove:
                on_remove(path)
            return True
        except OSError:
            return False

    needed = stat.S_IRWXU
    if (st.st_mode & needed) != needed:
        try:
            os.chmod(path, st.st_mode | needed)
        except OSError:
            return False

    ok = True
    try:
        entries = os.listdir(path)
    except OSError:
        return False

    for name in entries:
        if not _remove_path(os.path.join(path, name), on_remove):
            ok = False

    if ok:
        try:
            os.rmdir(path)
            if on_remove:
                on_remove(path)
        except OSError:
            ok = False

    return ok


def command_remove(args, configs: dict) -> None:  # noqa: ARG001
    dist_name = args.alias
    verbose = getattr(args, "verbose", False)

    container_dir = os.path.join(CONTAINERS_DIR, dist_name)
    rootfs_dir = os.path.join(container_dir, "rootfs")

    if not os.path.isdir(rootfs_dir):
        msg()
        msg(f"{C['BRED']}Error: container "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}' is not installed.{C['RST']}")
        msg()
        sys.exit(1)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Removing container "
        f"'{C['YELLOW']}{dist_name}{C['CYAN']}'...{C['RST']}")

    on_remove = None
    if verbose:
        def on_remove(path):
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Removed: '{path}'{C['RST']}")

    if not _remove_path(container_dir, on_remove):
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Finished with errors. Some files probably were not "
            f"deleted.{C['RST']}")
        sys.exit(1)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Finished removing the container.{C['RST']}")
