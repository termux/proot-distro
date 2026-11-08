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

"""CLI presentation adapter for shared registry image publication."""

import sys

from proot_distro.helpers.download import NETWORK_ERRORS
from proot_distro.message import C, crit_error, log_error, msg
from proot_distro.operation_errors import BusyFailure
from proot_distro.operations.context import OperationContext
from proot_distro.operations.errors import (
    ImageNotFoundFailure,
    PushValidationFailure,
)
from proot_distro.operations.push import push_image
from proot_distro.progress import clear_bar, fmt_size


def command_push(args):
    """Translate CLI arguments, publish an image, and render its summary."""
    quiet = bool(getattr(args, "quiet", False))
    try:
        result = push_image(
            getattr(args, "image_ref", None) or "",
            architecture=getattr(args, "override_arch", None),
            allow_insecure=bool(getattr(args, "allow_insecure", False)),
            context=OperationContext(
                verbose=bool(getattr(args, "verbose", False))
            ),
        )
    except (BusyFailure, ImageNotFoundFailure,
            PushValidationFailure) as exc:
        clear_bar()
        crit_error(str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        clear_bar()
        log_error("Aborted by user.")
        sys.exit(1)
    except NETWORK_ERRORS as exc:
        clear_bar()
        log_error(f"Network error: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        clear_bar()
        log_error(f"Error: {exc}")
        sys.exit(1)

    if quiet:
        return result

    msg()
    msg(f"{C['CYAN']}Repository: "
        f"{C['GREEN']}{result.registry}/{result.repository}{C['RST']}")
    msg(f"{C['CYAN']}Tag:        "
        f"{C['GREEN']}{result.tag}{C['RST']}")
    if result.manifest_digest:
        msg(f"{C['CYAN']}Digest:     "
            f"{C['GREEN']}{result.manifest_digest}{C['RST']}")
    msg(f"{C['CYAN']}Uploaded:   "
        f"{C['GREEN']}{fmt_size(result.bytes_uploaded)}{C['RST']}")
    msg()
    return result


__all__ = ("command_push",)
