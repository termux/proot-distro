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

import sys
from types import SimpleNamespace

from proot_distro.message import log_info, log_error, crit_error
from proot_distro.commands.remove import _remove_path
from proot_distro.commands.install import command_install
from proot_distro.locking import ContainerLock
from proot_distro.names import require_valid_name
from proot_distro.paths import (
    container_is_installed, container_rootfs, read_container_manifest,
)


def command_reset(args) -> None:
    """Wipe the rootfs and reinstall from the cached image manifest."""
    container_name = args.container_name

    require_valid_name(container_name)

    rootfs_dir = container_rootfs(container_name)

    # Walked down to rather than named: os.path.isdir() followed a
    # `containers/<name>` a guest had re-pointed, and reset would then
    # have gone on to clear a rootfs inside whatever it led to.
    if not container_is_installed(container_name):
        crit_error(f"container '{container_name}' is not installed.")
        sys.exit(1)

    # Read original image_ref and arch from the stored manifest, through
    # the container directory's own descriptor: what gets reinstalled
    # comes out of this file, and the name is a guest-writable one
    # (see paths.open_container_manifest).
    image_ref = None
    override_arch = None
    try:
        manifest_data = read_container_manifest(container_name)
        image_ref = manifest_data.get("image_ref")
        override_arch = manifest_data.get("arch")
    except (OSError, ValueError):
        pass

    if not image_ref:
        crit_error(f"container '{container_name}' has no OCI "
                   f"manifest. Reset is supported for OCI images only.")
        sys.exit(1)

    with ContainerLock(container_name, exclusive=True, command="reset"):
        log_info(f"Removing rootfs of '{container_name}'...")

        # No shutil.rmtree() fallback behind this. It could only ever fail
        # where _remove_path already had — it does not chmod a sealed
        # subtree, so it does strictly less — and being plain recursion it
        # would turn a rootfs deeper than the interpreter's stack into a
        # RecursionError right where the iterative walk had just avoided
        # one. install() refuses a rootfs that is still there, which is the
        # report the user gets.
        if not _remove_path(rootfs_dir):
            log_error("Finished with errors. Some files could not be deleted. "
                      "Proceeding anyway.")

        # Rebuild args for install: reuse dist name and the stored image/arch.
        command_install(
            SimpleNamespace(
                image_ref=image_ref,
                custom_container_name=container_name,
                override_arch=override_arch,
            )
        )
