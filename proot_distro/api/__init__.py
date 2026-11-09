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

from proot_distro.api.cancellation import CancellationToken
from proot_distro.api.errors import (
    AlreadyExistsError,
    AuthenticationError,
    BusyError,
    IntegrityError,
    InvalidArgumentError,
    NetworkError,
    NotFoundError,
    OperationError,
    OperationCancelledError,
    PartialOperationError,
    ProotDistroError,
    SessionStartupTimeoutError,
    UnsupportedOperationError,
)
from proot_distro.api.config import RuntimeConfig
from proot_distro.api.credentials import (
    CredentialProvider,
    CredentialRequest,
    RegistryCredentials,
)
from proot_distro.api.events import EventHandler, EventKind, OperationEvent
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


# This version is independent of the package release. The major component
# changes for incompatible public-API changes; the minor component changes for
# backward-compatible additions.
API_VERSION: str = "1.0"


__all__ = (
    "API_VERSION",
    "AlreadyExistsError",
    "AuthenticationError",
    "BackupResult",
    "BuildResult",
    "BusyError",
    "CacheCleanupResult",
    "CancellationToken",
    "ContainerInfo",
    "CredentialProvider",
    "CredentialRequest",
    "DetachedSession",
    "EventHandler",
    "EventKind",
    "ImageInfo",
    "ImageRemovalResult",
    "IntegrityError",
    "InvalidArgumentError",
    "NetworkError",
    "NotFoundError",
    "OperationCancelledError",
    "OperationError",
    "OperationEvent",
    "PartialOperationError",
    "ProotDistro",
    "ProotDistroError",
    "PushResult",
    "RegistryCredentials",
    "RestoreResult",
    "RuntimeConfig",
    "SearchHit",
    "SearchResult",
    "SessionCommand",
    "SessionInfo",
    "SessionProcess",
    "SessionSignalResult",
    "SessionStartupTimeoutError",
    "TransferResult",
    "UnsupportedOperationError",
)
