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

from proot_distro.lazy_exports import lazy_exports


# Each name and the submodule defining it. Nothing is imported until a
# name is used: a worker running one service must not load the others.
_EXPORTS = {
    "ActiveSession": "models",
    "ArchiveBackup": "models",
    "ArchiveRestore": "models",
    "CacheCleanup": "models",
    "CachedImage": "models",
    "ContainerRemoval": "models",
    "ContainerInstallation": "models",
    "ContainerRename": "models",
    "ImageRemoval": "models",
    "ImageBuild": "models",
    "ImagePush": "models",
    "InstalledContainer": "models",
    "OperationContext": "context",
    "PreparedSession": "session_prepare",
    "SessionLaunch": "models",
    "SessionOptions": "session_prepare",
    "SessionSignal": "models",
    "Transfer": "models",
    "backup_container": "backup",
    "build_image": "build",
    "clear_cache": "cache",
    "copy_path": "copy",
    "install_container": "install",
    "list_containers": "inventory",
    "list_images": "inventory",
    "list_sessions": "inventory",
    "move_path": "copy",
    "push_image": "push",
    "remove_container": "containers",
    "remove_image": "images",
    "restore_container": "restore",
    "reset_container": "reset",
    "signal_sessions": "session_signals",
    "prepare_session": "session_prepare",
    "session_options": "session_prepare",
    "rename_container": "containers",
    "sync_path": "sync",
}

__all__ = tuple(_EXPORTS)

__getattr__, __dir__ = lazy_exports(__name__, _EXPORTS)
