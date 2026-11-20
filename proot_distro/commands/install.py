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

"""CLI adapter for the shared container installation service."""

import sys

from proot_distro.constants import PROGRAM_NAME
from proot_distro.message import C, crit_error, msg
from proot_distro.operation_errors import (
    AlreadyExistsFailure,
    BusyFailure,
    NotFoundFailure,
    OperationFailure,
)
from proot_distro.operations.context import OperationContext
from proot_distro.operations.errors import InstallValidationFailure
from proot_distro.operations.install import (
    _derive_local_name,
    _is_local_path,
    _is_url,
    _resolve_install_name,
    install_container,
)


def command_install(args) -> None:
    """Validate CLI preferences and invoke shared installation logic."""
    try:
        result = install_container(
            args.image_ref,
            name=getattr(args, "custom_container_name", None),
            architecture=getattr(args, "override_arch", None),
            allow_insecure=bool(getattr(args, "allow_insecure", False)),
            context=OperationContext(verbose=getattr(args, "verbose", False)),
        )
    except AlreadyExistsFailure as exc:
        msg()
        crit_error(str(exc))
        msg()
        name = getattr(exc, "name", None)
        if name:
            msg(f"{C['CYAN']}Start shell: "
                f"{C['GREEN']}{PROGRAM_NAME} login {name}{C['RST']}")
            msg(f"{C['CYAN']}Reinstall:   "
                f"{C['GREEN']}{PROGRAM_NAME} reset {name}{C['RST']}")
            msg(f"{C['CYAN']}Uninstall:   "
                f"{C['GREEN']}{PROGRAM_NAME} remove {name}{C['RST']}")
            msg()
        sys.exit(1)
    except (BusyFailure, NotFoundFailure, InstallValidationFailure) as exc:
        crit_error(str(exc))
        sys.exit(1)
    except OperationFailure as exc:
        if not getattr(exc, "reported", False):
            crit_error(str(exc))
        sys.exit(1)
    except RuntimeError:
        # The service emitted the detailed install failure before cleanup.
        sys.exit(1)
    except KeyboardInterrupt:
        sys.exit(1)

    _print_install_summary(result.name, result.has_entrypoint)


def _print_install_summary(name, has_entrypoint) -> None:
    """Render the common post-install shell and entrypoint hints."""
    msg()
    shell_label = "Start shell:   " if has_entrypoint else "Start shell:"
    msg(f"{C['CYAN']}{shell_label} "
        f"{C['GREEN']}{PROGRAM_NAME} login {name}{C['RST']}")
    if has_entrypoint:
        msg(f"{C['CYAN']}Run entrypoint: "
            f"{C['GREEN']}{PROGRAM_NAME} run {name}{C['RST']}")
    msg()


__all__ = ("command_install",)
