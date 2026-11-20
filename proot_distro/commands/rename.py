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

# Architecture: CLI adapter for operations.rename_container. This module owns
# argparse-era name validation and terminal error presentation; the shared
# service owns locks, descriptor-safe mutation, l2s rewriting, events, and its
# immutable result.
#
# Both names are entries of CONTAINERS_DIR, which is guest-writable on
# Termux -- it sits under the $TERMUX_PREFIX bound read-write into every
# non-isolated container -- so neither is composed into a path and
# handed to os.rename(). The directory is opened once through statedir's
# O_NOFOLLOW walk and the rename runs src_dir_fd/dst_dir_fd on it: a
# `containers/<old>` a guest had left behind as a symlink used to be
# moved as the link, after which the l2s rewrite walked -- and wrote
# into -- whatever it pointed at. The service retains the descriptor-based
# implementation that closes that attack.

import sys

from proot_distro.message import log_error, crit_error
from proot_distro.names import require_valid_name
from proot_distro.operation_errors import (
    AlreadyExistsFailure, BusyFailure, NotFoundFailure, OperationFailure,
)
from proot_distro.operations import rename_container


def command_rename(args) -> None:
    """Rename a container directory and rewrite its l2s symlinks."""
    orig = args.orig_name
    new = args.new_name

    if orig == new:
        crit_error("original and new names must differ.")
        sys.exit(1)

    require_valid_name(orig, kind="original container name")
    require_valid_name(new, kind="new container name")

    try:
        rename_container(orig, new)
    except (AlreadyExistsFailure, BusyFailure, NotFoundFailure) as exc:
        crit_error(str(exc))
        sys.exit(1)
    except OperationFailure as exc:
        reporter = log_error if str(exc).startswith("Failed") else crit_error
        reporter(str(exc))
        sys.exit(1)
