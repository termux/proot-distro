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

# Architecture: One-shot migration from the legacy `installed-rootfs/<name>`
# layout to `containers/<name>/rootfs`. Runs on first login of a legacy
# container; subsequent logins skip silently. The post-rename walk
# rewrites every l2s symlink target that still points at the old
# location, with SIGINT/SIGQUIT guarded by l2s.rewrite_l2s_targets so
# the user cannot leave the container half-rewritten via Ctrl-C.

import os
import stat

from proot_distro import dirfd, statedir
from proot_distro.constants import LEGACY_ROOTFS_DIR
from proot_distro.message import log_info, log_error
from proot_distro.l2s import rewrite_l2s_targets
from proot_distro.paths import (
    container_is_installed, container_rootfs, open_container_dir,
)


def _legacy_dir_fd(container_name: str):
    """Open installed-rootfs if it holds a real directory for *name*. Or None.

    Both ends of the move are entries of the runtime tree, which is
    guest-writable on Termux, so neither is composed into a path:
    os.path.isdir() answered "yes" for a
    `installed-rootfs/<name> -> <host dir>` a container had left behind
    and os.rename() then moved the link into place as the container's
    rootfs, after which everything -- the l2s rewrite here, and every
    session afterwards -- worked inside that host directory.
    """
    try:
        legacy_fd = statedir.open_state_dir(LEGACY_ROOTFS_DIR)
    except OSError:
        return None
    try:
        st = dirfd.lstat_at(legacy_fd, container_name)
    except OSError:
        os.close(legacy_fd)
        return None
    if not stat.S_ISDIR(st.st_mode):
        os.close(legacy_fd)
        return None
    return legacy_fd


def migrate_legacy_rootfs(container_name: str) -> None:
    """Move legacy installed-rootfs/<name> to containers/<name>/rootfs."""
    legacy_fd = _legacy_dir_fd(container_name)
    if legacy_fd is None:
        return

    try:
        if container_is_installed(container_name):
            return              # already migrated

        log_info(f"Migrating legacy container '{container_name}'...")
        try:
            new_fd = open_container_dir(container_name, create=True)
        except OSError as exc:
            log_error(f"Error: {exc}")
            return
        try:
            try:
                os.rename(container_name, "rootfs",
                          src_dir_fd=legacy_fd, dst_dir_fd=new_fd)
            except OSError as exc:
                log_error(f"Error: {exc}")
                return
            try:
                rootfs_fd = dirfd.descend_at(new_fd, ("rootfs",))
            except OSError as exc:
                log_error(f"Error: {exc}")
                return
        finally:
            os.close(new_fd)
    finally:
        os.close(legacy_fd)

    try:
        rewrite_l2s_targets(
            rootfs_fd,
            container_rootfs(container_name),
            os.path.join(LEGACY_ROOTFS_DIR, container_name),
        )
    finally:
        os.close(rootfs_fd)
    log_info("Migration complete.")
