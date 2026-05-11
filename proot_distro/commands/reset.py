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

# Architecture: Rebuilds a container rootfs from the image reference stored
# in containers/<name>/manifest.json. Only the rootfs/ subdirectory is
# removed; manifest.json is preserved and re-used by install. If the
# manifest is absent the reset falls back to pulling 'latest' from Docker
# Hub using the container name as the image name.

import json
import os
import shutil
import sys

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.colors import C, msg
from proot_distro.commands.remove import _remove_path
from proot_distro.commands.install import command_install


def command_reset(args, configs: dict) -> None:
    dist_name = args.alias

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
        # Fallback: use the container name as the image name with :latest.
        image_ref = f"{dist_name}:latest"
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"No manifest found; will pull "
            f"'{C['YELLOW']}{image_ref}{C['CYAN']}'...{C['RST']}")

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
