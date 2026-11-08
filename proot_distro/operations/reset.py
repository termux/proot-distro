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

"""Shared OCI-backed container reset service."""

from proot_distro.locking import ContainerLock
from proot_distro.names import NAME_RULE_HINT, is_valid_name
from proot_distro.operation_errors import NotFoundFailure, UnsupportedFailure
from proot_distro.operations.containers import remove_path
from proot_distro.operations.context import OperationContext
from proot_distro.operations.install import install_container
from proot_distro.operations.locks import operation_lock
from proot_distro.paths import (
    container_image_origin,
    container_is_installed,
    container_rootfs,
)
from proot_distro.shm import shm_dir
from proot_distro.statedir import remove_state_tree


def reset_container(name, *, context=None):
    """Replace a container rootfs from its recorded OCI image origin."""
    context = context if context is not None else OperationContext()
    if not isinstance(name, str) or not is_valid_name(name):
        raise NotFoundFailure(
            f"container name '{name}' is not valid. {NAME_RULE_HINT}"
        )
    if not container_is_installed(name, raise_errors=True):
        raise NotFoundFailure(f"container '{name}' is not installed.")

    image_ref, architecture = container_image_origin(name)
    if not image_ref:
        raise UnsupportedFailure(
            f"container '{name}' has no OCI manifest. Reset is supported "
            f"for OCI images only."
        )

    rootfs_dir = container_rootfs(name)
    lock = ContainerLock(name, exclusive=True, command="reset")
    with operation_lock(lock):
        context.info(
            f"Removing rootfs of '{name}'...",
            phase="reset", resource=name,
        )
        if not remove_path(rootfs_dir):
            context.error(
                "Finished with errors. Some files could not be deleted. "
                "Proceeding anyway.",
                phase="reset", resource=name,
            )
        remove_state_tree(shm_dir(rootfs_dir))
        return install_container(
            image_ref,
            name=name,
            architecture=architecture,
            context=context,
        )


__all__ = ("reset_container",)
