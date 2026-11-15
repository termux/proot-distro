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

"""Typed internal failures carrying structured service diagnostics."""

from proot_distro.helpers.docker.transport import RegistryAuthenticationError
from proot_distro.operation_errors import (
    IntegrityFailure,
    NetworkFailure,
    NotFoundFailure,
    OperationFailure,
)
from proot_distro.session_errors import (
    SessionExecutionFailure,
    SessionNotFoundFailure,
    SessionValidationFailure,
)


class ImageNotFoundFailure(NotFoundFailure):
    """No cached image matched, with immutable near-miss records."""

    def __init__(self, identifier, architecture, similar=()):
        scope = f" for architecture '{architecture}'" if architecture else ""
        super().__init__(f"no cached image matches '{identifier}'{scope}")
        self.identifier = identifier
        self.architecture = architecture
        self.similar = tuple(similar)


class BuildNetworkFailure(OperationFailure, NetworkFailure):
    """A build step could not reach a remote service.

    Both bases on purpose: the command adapter handles every build failure
    as an ``OperationFailure``, while API callers need the network kind.
    """


class BuildAuthenticationFailure(OperationFailure, RegistryAuthenticationError):
    """A registry refused a build's credentials (see BuildNetworkFailure)."""


class BuildIntegrityFailure(OperationFailure, IntegrityFailure):
    """Content a build fetched failed validation (see BuildNetworkFailure)."""


def build_failure(exc: BaseException, message: str) -> OperationFailure:
    """Return the failure for a build-engine error, keeping its kind.

    The engine flattens registry and download errors into a plain
    ``BuildError`` whose ``__cause__`` is the typed original. Without this
    the kind was lost and every unreachable base image looked like a
    generic failure to API callers.
    """
    cause = exc.__cause__
    for _ in range(8):
        if cause is None:
            break
        for source, failure in (
            (RegistryAuthenticationError, BuildAuthenticationFailure),
            (NetworkFailure, BuildNetworkFailure),
            (IntegrityFailure, BuildIntegrityFailure),
        ):
            if isinstance(cause, source):
                return failure(message)
        cause = cause.__cause__
    return OperationFailure(message)


class AmbiguousImageFailure(OperationFailure):
    """An image-ID prefix selected more than one image ID."""

    def __init__(self, identifier, matches):
        self.identifier = identifier
        self.matches = tuple(matches)
        ids = {item.cache_image_id for item in self.matches}
        super().__init__(
            f"image ID '{identifier}' is ambiguous - it matches "
            f"{len(ids)} images"
        )


class TransferPartialFailure(OperationFailure):
    """A transfer skipped entries after making observable progress."""

    def __init__(self, message, result):
        super().__init__(message)
        self.result = result


class TransferValidationFailure(OperationFailure):
    """A transfer path or option cannot be used for the requested action."""


class InstallValidationFailure(OperationFailure):
    """An install source, name, or architecture is not usable."""


class BuildValidationFailure(OperationFailure):
    """A build path, Dockerfile, tag, or option is not usable."""


class PushValidationFailure(OperationFailure):
    """An image reference, architecture, or push option is not usable."""


class SessionSignalValidationFailure(OperationFailure):
    """A session target or signal selection is not usable."""

    def __init__(self, message, *, show_help=True):
        super().__init__(message)
        self.show_help = show_help


class SessionSignalFailure(OperationFailure):
    """One or more tracked processes survived a termination request."""

    def __init__(self, result):
        self.result = result
        names = ", ".join(str(pid) for pid in result.survivors)
        super().__init__(
            f"{len(result.survivors)} process(es) survived {result.signal} "
            f"and could not be stopped: {names}."
        )


__all__ = (
    "AmbiguousImageFailure",
    "BuildAuthenticationFailure",
    "BuildIntegrityFailure",
    "BuildNetworkFailure",
    "BuildValidationFailure",
    "build_failure",
    "ImageNotFoundFailure",
    "InstallValidationFailure",
    "PushValidationFailure",
    "SessionSignalFailure",
    "SessionSignalValidationFailure",
    "SessionExecutionFailure",
    "SessionNotFoundFailure",
    "SessionValidationFailure",
    "TransferPartialFailure",
    "TransferValidationFailure",
)
