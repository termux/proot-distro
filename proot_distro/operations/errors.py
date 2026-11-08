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

from proot_distro.operation_errors import NotFoundFailure, OperationFailure


class ImageNotFoundFailure(NotFoundFailure):
    """No cached image matched, with immutable near-miss records."""

    def __init__(self, identifier, architecture, similar=()):
        scope = f" for architecture '{architecture}'" if architecture else ""
        super().__init__(f"no cached image matches '{identifier}'{scope}")
        self.identifier = identifier
        self.architecture = architecture
        self.similar = tuple(similar)


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


__all__ = (
    "AmbiguousImageFailure",
    "BuildValidationFailure",
    "ImageNotFoundFailure",
    "InstallValidationFailure",
    "TransferPartialFailure",
    "TransferValidationFailure",
)
