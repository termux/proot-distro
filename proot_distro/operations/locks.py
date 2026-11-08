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

"""Process-control-free acquisition of existing lock primitives."""

from contextlib import contextmanager

from proot_distro.operation_errors import BusyFailure


@contextmanager
def operation_lock(lock):
    """Acquire *lock* or raise :class:`BusyFailure`; always release it."""
    if not lock.acquire():
        detail = lock.blocked_detail()
        if detail:
            message = f"cannot lock {lock._label} '{lock._display}': {detail}"
        else:
            message = (
                f"{lock._label} '{lock._display}' is busy"
                f"{lock.holder_hint()}."
            )
        raise BusyFailure(message)
    try:
        yield lock
    finally:
        lock.release()


__all__ = ("operation_lock",)
