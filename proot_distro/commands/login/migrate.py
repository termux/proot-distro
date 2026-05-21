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

from proot_distro.constants import LEGACY_ROOTFS_DIR
from proot_distro.message import log_info, log_error
from proot_distro.l2s import rewrite_l2s_targets
from proot_distro.paths import container_dir, container_rootfs


def migrate_legacy_rootfs(container_name: str) -> None:
    """Move legacy installed-rootfs/<name> to containers/<name>/rootfs."""
    legacy_path = os.path.join(LEGACY_ROOTFS_DIR, container_name)
    if not os.path.isdir(legacy_path):
        return

    new_rootfs = container_rootfs(container_name)

    if os.path.isdir(new_rootfs):
        return  # already migrated

    log_info(f"Migrating legacy container '{container_name}'...")
    try:
        os.makedirs(container_dir(container_name), exist_ok=True)
        os.rename(legacy_path, new_rootfs)
    except OSError as exc:
        log_error(f"Error: {exc}")
        return

    rewrite_l2s_targets(new_rootfs, legacy_path)
    log_info("Migration complete.")
