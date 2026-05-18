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

# Architecture: Rebuilds a container rootfs from the image reference stored
# in containers/<name>/manifest.json. Only the rootfs/ subdirectory is
# removed; manifest.json is preserved and re-used by install. Containers
# without a manifest (plain tarball installs, legacy rootfs) are rejected —
# reset requires an OCI image_ref to know what to pull.

import json
import os
import shutil
import sys

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.colors import C, msg
from proot_distro.commands.remove import _remove_path
from proot_distro.commands.install import command_install, _validate_name
from proot_distro.locking import ContainerLock


def command_reset(args, configs: dict) -> None:
    dist_name = args.alias

    if not _validate_name(dist_name):
        msg()
        msg(f"{C['BRED']}Error: container name "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}' is not valid. "
            f"It must begin with a letter or digit and contain only "
            f"letters, digits, underscores, dots, or hyphens.{C['RST']}")
        msg()
        sys.exit(1)

    container_dir = os.path.join(CONTAINERS_DIR, dist_name)
    rootfs_dir = os.path.join(container_dir, "rootfs")
    manifest_path = os.path.join(container_dir, "manifest.json")

    if not os.path.isdir(rootfs_dir):
        msg()
        msg(f"{C['BRED']}Error: container "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}' is not installed.{C['RST']}")
        msg()
        sys.exit(1)

    # Read original image_ref and arch from the stored manifest.
    image_ref = None
    override_arch = None
    if os.path.isfile(manifest_path):
        try:
            with open(manifest_path) as fh:
                manifest_data = json.load(fh)
            image_ref = manifest_data.get("image_ref")
            override_arch = manifest_data.get("arch")
        except (OSError, json.JSONDecodeError):
            pass

    if not image_ref:
        msg()
        msg(f"{C['BRED']}Error: container "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}' has no OCI manifest. "
            f"Reset is supported for OCI images only.{C['RST']}")
        msg()
        sys.exit(1)

    with ContainerLock(dist_name, exclusive=True, command="reset"):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Removing rootfs of "
            f"'{C['YELLOW']}{dist_name}{C['CYAN']}'...{C['RST']}")

        if not _remove_path(rootfs_dir):
            msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
                f"Finished with errors. Some files could not be deleted. "
                f"Proceeding anyway.{C['RST']}")
            try:
                shutil.rmtree(rootfs_dir, ignore_errors=True)
            except OSError:
                pass

        # Rebuild args for install: reuse dist name and the stored image/arch.
        class _ResetArgs:
            alias = image_ref
            custom_dist_name = dist_name

        reset_args = _ResetArgs()
        reset_args.override_arch = override_arch

        command_install(reset_args, configs)
