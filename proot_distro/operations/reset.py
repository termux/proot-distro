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

from proot_distro.helpers.docker.refs import InvalidReference, parse_reference
from proot_distro.locking import ContainerLock
from proot_distro.message import quote_path
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


def _registry_origin(name):
    """Return ``(image_ref, arch)`` to reinstall *name* from, or raise.

    The reference comes out of the container's manifest.json, which a
    guest can rewrite on Termux, and it is handed to the install service,
    which also installs from a local path or a URL. A recorded reference
    is honoured only when it parses as a registry reference: anything else
    (a host path, a URL) would have reset extract a file of the guest's
    choosing from the host, or fetch one, into the guest's own rootfs.
    """
    image_ref, architecture = container_image_origin(name)
    if not image_ref:
        raise UnsupportedFailure(
            f"container '{name}' has no OCI manifest. Reset is supported "
            f"for OCI images only."
        )
    try:
        parse_reference(image_ref)
    except InvalidReference:
        raise UnsupportedFailure(
            f"container '{name}' records '{quote_path(image_ref)}', which is "
            "not a registry image reference. Reset only reinstalls images "
            "from a registry."
        ) from None
    return image_ref, architecture


def reset_container(name, *, context=None):
    """Replace a container rootfs from its recorded OCI image origin."""
    context = context if context is not None else OperationContext()
    if not isinstance(name, str) or not is_valid_name(name):
        raise NotFoundFailure(
            f"container name '{name}' is not valid. {NAME_RULE_HINT}"
        )
    if not container_is_installed(name, raise_errors=True):
        raise NotFoundFailure(f"container '{name}' is not installed.")

    # Checked before the lock for a quick refusal, and read again under
    # it, so the reference reinstalled is the one the manifest holds once
    # this container's own sessions are gone. Another non-isolated
    # container can still rewrite the file, which is why the value is
    # validated where it is read and only that in-memory value is used.
    _registry_origin(name)

    rootfs_dir = container_rootfs(name)
    lock = ContainerLock(name, exclusive=True, command="reset")
    with operation_lock(lock):
        image_ref, architecture = _registry_origin(name)
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
