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

"""Shared container lifecycle operations.

Inputs have already passed adapter-specific syntax validation. These services
own secure state resolution, locks, mutations, events, and immutable results.
They raise internal typed failures and never call ``sys.exit()``.
"""

import os
import stat

from proot_distro import dirfd, statedir
from proot_distro.constants import CONTAINERS_DIR
from proot_distro.l2s import rewrite_l2s_targets
from proot_distro.locking import ContainerLock
from proot_distro.message import quote_path
from proot_distro.operation_errors import (
    AlreadyExistsFailure,
    NotFoundFailure,
    OperationFailure,
)
from proot_distro.operations.context import OperationContext
from proot_distro.operations.locks import operation_lock
from proot_distro.operations.models import ContainerRemoval, ContainerRename
from proot_distro.paths import (
    container_dir,
    container_entry_lstat,
    container_is_installed,
    container_rootfs,
    open_container_dir,
)


def _context(value):
    return value if value is not None else OperationContext()


def remove_path(path: str, on_remove=None) -> bool:
    """Remove a state tree iteratively without following planted links.

    The explicit-stack walk is implemented by ``remove_state_tree`` so a guest
    cannot exhaust Python recursion with a deep rootfs. It reaches the parent
    from a trusted state root and addresses every entry by descriptor; chmod
    and removal therefore cannot follow a symlink planted toward a host path.
    The callback receives the display path of each removed entry.
    """
    return statedir.remove_state_tree(
        path,
        on_remove=(
            None if on_remove is None
            else lambda rel: on_remove(os.path.join(path, rel) if rel else path)
        ),
    )


def remove_container(
    name: str, *, context: OperationContext = None,
) -> ContainerRemoval:
    """Remove one container-state entry and return what was removed."""
    context = _context(context)
    entry_st = container_entry_lstat(name)
    if entry_st is None:
        raise NotFoundFailure(f"container '{name}' is not installed")

    lock = ContainerLock(name, exclusive=True, command="remove")
    with operation_lock(lock):
        was_directory = stat.S_ISDIR(entry_st.st_mode)
        if not was_directory:
            context.warning(
                f"'{quote_path(container_dir(name))}' is not a container "
                f"directory; removing that entry itself. Whatever it names "
                f"is left where it is.",
                phase="remove", resource=name,
            )
        context.info(
            f"Removing container '{name}'...",
            phase="remove", resource=name,
        )

        on_remove = None
        if context.verbose:
            def on_remove(path):
                context.info(
                    f"Removed: '{quote_path(path)}'",
                    phase="remove", resource=name,
                )

        if not remove_path(container_dir(name), on_remove):
            raise OperationFailure(
                "Finished with errors. Some files probably were not deleted."
            )

    context.info(
        "Finished removing the container.",
        phase="remove", resource=name,
    )
    return ContainerRemoval(name, was_directory)


def rename_container(
    old_name: str,
    new_name: str,
    *,
    context: OperationContext = None,
) -> ContainerRename:
    """Rename a container and rewrite link2symlink targets securely."""
    context = _context(context)
    if old_name == new_name:
        raise OperationFailure("original and new names must differ")
    if not container_is_installed(old_name, raise_errors=True):
        raise NotFoundFailure(f"container '{old_name}' is not installed")

    try:
        os.close(open_container_dir(new_name, raise_errors=True))
    except FileNotFoundError:
        pass
    else:
        raise AlreadyExistsFailure(f"container '{new_name}' already exists")

    first, second = (
        (old_name, new_name) if old_name < new_name else (new_name, old_name)
    )
    with operation_lock(ContainerLock(
        first, exclusive=True, command="rename"
    )):
        with operation_lock(ContainerLock(
            second, exclusive=True, command="rename"
        )):
            _rename_locked(old_name, new_name, context)
    return ContainerRename(old_name, new_name)


def _rename_locked(
    old_name: str, new_name: str, context: OperationContext,
) -> None:
    context.info(
        f"Renaming '{old_name}' to '{new_name}'...",
        phase="rename", resource=new_name,
    )
    try:
        containers_fd = statedir.open_state_dir(CONTAINERS_DIR, create=True)
    except OSError as exc:
        raise OperationFailure(f"Failed to rename container: {exc}") from exc
    try:
        try:
            os.rename(
                old_name, new_name,
                src_dir_fd=containers_fd, dst_dir_fd=containers_fd,
            )
        except OSError as exc:
            raise OperationFailure(
                f"Failed to rename container: {exc}"
            ) from exc
        try:
            rootfs_fd = dirfd.descend_at(
                containers_fd, (new_name, "rootfs")
            )
        except OSError:
            rootfs_fd = None
    finally:
        os.close(containers_fd)

    if rootfs_fd is not None:
        try:
            rewrite_l2s_targets(
                rootfs_fd, container_rootfs(new_name),
                container_rootfs(old_name),
            )
        finally:
            os.close(rootfs_fd)
    context.info(
        "Finished renaming the container.",
        phase="rename", resource=new_name,
    )


__all__ = ("remove_container", "remove_path", "rename_container")
