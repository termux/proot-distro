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
#
# Both names are entries of CONTAINERS_DIR, which is guest-writable on
# Termux -- it sits under the $TERMUX_PREFIX bound read-write into every
# non-isolated container -- so neither is composed into a path and
# handed to os.rename(). The directory is opened once through statedir's
# O_NOFOLLOW walk and the rename runs src_dir_fd/dst_dir_fd on it: a
# `containers/<old>` a guest had left behind as a symlink used to be
# moved as the link, after which the l2s rewrite walked -- and wrote
# into -- whatever it pointed at.

import os
import sys

from proot_distro import dirfd, statedir
from proot_distro.constants import CONTAINERS_DIR
from proot_distro.message import log_info, log_error, crit_error
from proot_distro.l2s import rewrite_l2s_targets
from proot_distro.locking import ContainerLock
from proot_distro.names import require_valid_name
from proot_distro.paths import (
    container_is_installed, container_rootfs, open_container_dir,
)


def command_rename(args) -> None:
    """Rename a container directory and rewrite its l2s symlinks."""
    orig = args.orig_name
    new = args.new_name

    if orig == new:
        crit_error("original and new names must differ.")
        sys.exit(1)

    require_valid_name(orig, kind="original container name")
    require_valid_name(new, kind="new container name")

    # Both questions are asked of the walk, never of a composed path:
    # os.path.isdir() followed a planted `containers/<name>` in either
    # direction -- reporting the source installed when it is a link to a
    # host directory, or the destination free when it is one.
    if not container_is_installed(orig):
        crit_error(f"container '{orig}' is not installed.")
        sys.exit(1)

    try:
        os.close(open_container_dir(new))
    except FileNotFoundError:
        pass                        # the name is free, which is the point
    else:
        crit_error(f"container '{new}' already exists.")
        sys.exit(1)

    # Acquire locks in sorted order to ensure consistent ordering.
    first, second = (orig, new) if orig < new else (new, orig)
    with ContainerLock(first, exclusive=True, command="rename"):
        with ContainerLock(second, exclusive=True, command="rename"):
            _do_rename(orig, new)


def _do_rename(orig: str, new: str) -> None:
    log_info(f"Renaming '{orig}' to '{new}'...")
    try:
        containers_fd = statedir.open_state_dir(CONTAINERS_DIR, create=True)
    except OSError as exc:
        log_error(f"Failed to rename container: {exc}")
        sys.exit(1)
    try:
        try:
            os.rename(orig, new,
                      src_dir_fd=containers_fd, dst_dir_fd=containers_fd)
        except OSError as exc:
            log_error(f"Failed to rename container: {exc}")
            sys.exit(1)

        # The rewrite walks the tree that has just moved and writes into
        # it, so it takes the descriptor rather than the new path.
        try:
            rootfs_fd = dirfd.descend_at(containers_fd, (new, "rootfs"))
        except OSError:
            rootfs_fd = None
    finally:
        os.close(containers_fd)

    if rootfs_fd is not None:
        try:
            rewrite_l2s_targets(rootfs_fd, container_rootfs(new),
                                container_rootfs(orig))
        finally:
            os.close(rootfs_fd)
    log_info("Finished renaming the container.")
