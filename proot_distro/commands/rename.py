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

# Architecture: Renames a container directory (containers/<old> to
# containers/<new>) and updates any proot link2symlink (l2s) symlinks
# that point into the old rootfs path. Name validation goes through
# the shared names module so format rules stay consistent across
# every command that accepts a container identifier.

import os
import sys

from proot_distro.message import log_info, log_error, crit_error
from proot_distro.l2s import rewrite_l2s_targets
from proot_distro.locking import ContainerLock
from proot_distro.names import require_valid_name
from proot_distro.paths import container_dir, container_rootfs


def command_rename(args) -> None:
    """Rename a container directory and rewrite its l2s symlinks."""
    orig = args.orig_name
    new = args.new_name

    if orig == new:
        crit_error("original and new names must differ.")
        sys.exit(1)

    require_valid_name(orig, kind="original container name")
    require_valid_name(new, kind="new container name")

    orig_dir = container_dir(orig)
    new_dir = container_dir(new)
    orig_rootfs = container_rootfs(orig)
    new_rootfs = container_rootfs(new)

    if not os.path.isdir(orig_rootfs):
        crit_error(f"container '{orig}' is not installed.")
        sys.exit(1)

    if os.path.isdir(new_dir):
        crit_error(f"container '{new}' already exists.")
        sys.exit(1)

    # Acquire locks in sorted order to ensure consistent ordering.
    first, second = (orig, new) if orig < new else (new, orig)
    with ContainerLock(first, exclusive=True, command="rename"):
        with ContainerLock(second, exclusive=True, command="rename"):
            _do_rename(orig, new, orig_dir, new_dir, new_rootfs, orig_rootfs)


def _do_rename(orig, new, orig_dir, new_dir, new_rootfs, orig_rootfs):
    log_info(f"Renaming '{orig}' to '{new}'...")
    try:
        os.rename(orig_dir, new_dir)
    except OSError as exc:
        log_error(f"Failed to rename container: {exc}")
        sys.exit(1)

    rewrite_l2s_targets(new_rootfs, orig_rootfs)
    log_info("Finished renaming the container.")
