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

"""Stable programmatic interface for PRoot-Distro.

This is an in-process Python API, not a client for a server. Only the names
re-exported from this module are covered by the public API compatibility
policy; modules elsewhere in the package remain implementation details.
"""

from proot_distro.api.errors import (
    AlreadyExistsError,
    AuthenticationError,
    BusyError,
    IntegrityError,
    InvalidArgumentError,
    NetworkError,
    NotFoundError,
    OperationError,
    PartialOperationError,
    ProotDistroError,
    UnsupportedOperationError,
)
from proot_distro.api.config import RuntimeConfig
from proot_distro.api.credentials import (
    CredentialProvider,
    CredentialRequest,
    RegistryCredentials,
)
from proot_distro.api.events import EventKind, OperationEvent
from proot_distro.api.models import (
    BackupResult,
    BuildResult,
    CacheCleanupResult,
    ContainerInfo,
    DetachedSession,
    ImageInfo,
    ImageRemovalResult,
    PushResult,
    SearchHit,
    SearchResult,
    SessionInfo,
    SessionCommand,
    SessionSignalResult,
    RestoreResult,
    TransferResult,
)
from proot_distro.api.program import ProotDistro
from proot_distro.api.process import SessionProcess


__all__ = (
    "AlreadyExistsError",
    "AuthenticationError",
    "BackupResult",
    "BuildResult",
    "BusyError",
    "CacheCleanupResult",
    "ContainerInfo",
    "CredentialProvider",
    "CredentialRequest",
    "DetachedSession",
    "EventKind",
    "ImageInfo",
    "ImageRemovalResult",
    "IntegrityError",
    "InvalidArgumentError",
    "NetworkError",
    "NotFoundError",
    "OperationError",
    "OperationEvent",
    "PartialOperationError",
    "ProotDistro",
    "ProotDistroError",
    "PushResult",
    "RuntimeConfig",
    "RegistryCredentials",
    "RestoreResult",
    "SearchHit",
    "SearchResult",
    "SessionInfo",
    "SessionCommand",
    "SessionProcess",
    "SessionSignalResult",
    "TransferResult",
    "UnsupportedOperationError",
)
