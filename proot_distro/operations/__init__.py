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

"""Shared operation services used by CLI and programmatic adapters.

Services own locking, secure state access, mutations, and structured results.
They never parse argparse namespaces, print terminal errors, or terminate the
process. The CLI and API workers remain adapters around this internal layer.
"""

from proot_distro.operations.containers import (
    remove_container,
    rename_container,
)
from proot_distro.operations.backup import backup_container
from proot_distro.operations.build import build_image
from proot_distro.operations.copy import copy_path, move_path
from proot_distro.operations.cache import clear_cache
from proot_distro.operations.images import remove_image
from proot_distro.operations.install import install_container
from proot_distro.operations.push import push_image
from proot_distro.operations.inventory import (
    list_containers,
    list_images,
    list_sessions,
)
from proot_distro.operations.sync import sync_path
from proot_distro.operations.restore import restore_container
from proot_distro.operations.reset import reset_container
from proot_distro.operations.context import OperationContext
from proot_distro.operations.models import (
    ActiveSession,
    ArchiveBackup,
    ArchiveRestore,
    CacheCleanup,
    CachedImage,
    ContainerRemoval,
    ContainerInstallation,
    ContainerRename,
    ImageRemoval,
    ImageBuild,
    ImagePush,
    InstalledContainer,
    Transfer,
)


__all__ = (
    "ActiveSession",
    "ArchiveBackup",
    "ArchiveRestore",
    "CacheCleanup",
    "CachedImage",
    "ContainerRemoval",
    "ContainerInstallation",
    "ContainerRename",
    "ImageRemoval",
    "ImageBuild",
    "ImagePush",
    "InstalledContainer",
    "OperationContext",
    "Transfer",
    "backup_container",
    "build_image",
    "clear_cache",
    "copy_path",
    "install_container",
    "list_containers",
    "list_images",
    "list_sessions",
    "move_path",
    "push_image",
    "remove_container",
    "remove_image",
    "restore_container",
    "reset_container",
    "rename_container",
    "sync_path",
)
