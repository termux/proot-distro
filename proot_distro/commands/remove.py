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

"""CLI adapter for shared container and cached-image removal services."""

import sys

from proot_distro.arch import normalize_arch
from proot_distro.constants import PROGRAM_NAME
from proot_distro.message import (
    C, crit_error, log_error, msg, quote_path,
)
from proot_distro.names import require_valid_name
from proot_distro.operation_errors import (
    BusyFailure, NotFoundFailure, OperationFailure,
)
from proot_distro.operations import (
    OperationContext, remove_container, remove_image,
)
from proot_distro.operations.containers import remove_path as _remove_path
from proot_distro.operations.errors import (
    AmbiguousImageFailure, ImageNotFoundFailure,
)


def command_remove(args) -> None:
    """Delete an installed container, or a cached image with --image."""
    if getattr(args, "image", False):
        _remove_image(args)
        return
    if getattr(args, "override_arch", None):
        crit_error("'--architecture' selects between cached image "
                   "variants; it requires '--image'.")
        sys.exit(1)
    _remove_container(args)


def _remove_container(args) -> None:
    """Validate CLI input and invoke shared container removal."""
    name = args.target
    require_valid_name(name)
    try:
        remove_container(
            name,
            context=OperationContext(verbose=getattr(args, "verbose", False)),
        )
    except (BusyFailure, NotFoundFailure) as exc:
        crit_error(str(exc))
        sys.exit(1)
    except OperationFailure as exc:
        log_error(str(exc))
        sys.exit(1)


def _remove_image(args) -> None:
    """Validate CLI input and invoke shared cached-image removal."""
    identifier = args.target
    raw_architecture = getattr(args, "override_arch", None)
    architecture = None
    if raw_architecture:
        architecture = normalize_arch(raw_architecture)
        if architecture is None:
            crit_error(
                f"unknown architecture '{raw_architecture}'. Valid values: "
                f"aarch64, arm, i686, riscv64, x86_64 (or Docker format: "
                f"linux/arm64, linux/amd64, linux/arm/v7, linux/386, "
                f"linux/riscv64)."
            )
            sys.exit(1)

    try:
        remove_image(
            identifier,
            architecture=architecture,
            context=OperationContext(verbose=getattr(args, "verbose", False)),
        )
    except ImageNotFoundFailure as exc:
        _report_no_match(exc)
        sys.exit(1)
    except AmbiguousImageFailure as exc:
        crit_error(f"{exc}:")
        _print_image_list(exc.matches)
        msg()
        sys.exit(1)
    except BusyFailure as exc:
        crit_error(str(exc))
        sys.exit(1)
    except OperationFailure as exc:
        log_error(str(exc))
        sys.exit(1)


def _report_no_match(exc: ImageNotFoundFailure) -> None:
    """Render a typed no-match failure and its immutable suggestions."""
    crit_error(f"{exc}.")
    msg()
    if exc.similar:
        msg(f"{C['CYAN']}Cached images with that name:{C['RST']}")
        _print_image_list(exc.similar)
    else:
        msg(f"{C['CYAN']}List cached images: "
            f"{C['GREEN']}{PROGRAM_NAME} list --image{C['RST']}")
    msg()


def _print_image_list(records) -> None:
    """Render immutable cached-image records as CLI suggestion bullets."""
    msg()
    for record in records:
        display = (
            quote_path(record.reference) if record.reference
            else (
                f"{quote_path(record.repository)}:<none>"
                if record.repository else quote_path(record.cache_key)
            )
        )
        architecture = (
            quote_path(record.architecture)
            if record.architecture else "unknown arch"
        )
        image_id = quote_path(record.cache_image_id[:12] or record.cache_key)
        msg(f"  {C['CYAN']}* {C['GREEN']}{display}"
            f"{C['CYAN']} ({architecture}, ID {image_id}){C['RST']}")


__all__ = ("command_remove",)
