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

"""Internal typed failures shared by helpers and the API worker.

They remain RuntimeError subclasses so existing CLI exception nets and
messages are unchanged. The API worker translates them to public exceptions.
"""


class NetworkFailure(RuntimeError):
    """A remote operation failed after retry policy was applied."""


class IntegrityFailure(RuntimeError):
    """Content failed digest or archive structure validation."""


class NotFoundFailure(RuntimeError):
    """Required state or cached content was absent."""


class BusyFailure(RuntimeError):
    """An operation could not acquire or safely infer lock state."""


class UnsupportedFailure(RuntimeError):
    """The requested format or operation is unsupported."""


__all__ = (
    "BusyFailure",
    "IntegrityFailure",
    "NetworkFailure",
    "NotFoundFailure",
    "UnsupportedFailure",
)
