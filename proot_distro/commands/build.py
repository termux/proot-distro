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

"""CLI presentation adapter for the shared Dockerfile build service."""

import os
import sys

from proot_distro.commands.install import _print_install_summary
from proot_distro.constants import PROGRAM_NAME
from proot_distro.helpers.dockerfile import MAX_DOCKERFILE_BYTES
from proot_distro.message import C, crit_error, log_error, msg
from proot_distro.operation_errors import (
    AlreadyExistsFailure,
    BusyFailure,
    NotFoundFailure,
    OperationFailure,
)
from proot_distro.operations.build import (
    _derive_tag_from_path,
    _make_build_tmp,
    build_image,
)
from proot_distro.operations.context import OperationContext
from proot_distro.operations.errors import BuildValidationFailure
from proot_distro.progress import fmt_size


def command_build(args) -> None:
    """Translate CLI arguments, invoke the build service, and render hints."""
    dockerfile_path = getattr(args, "dockerfile", None)
    dockerfile_file = getattr(args, "_api_dockerfile_file", None)
    if dockerfile_path == "-" and dockerfile_file is None:
        dockerfile_file = sys.stdin.buffer
    outputs = list(getattr(args, "outputs", None) or [])
    outputs.extend(list(getattr(args, "_api_output_files", None) or []))
    quiet = bool(getattr(args, "quiet", False))
    try:
        result = build_image(
            getattr(args, "path", None) or ".",
            dockerfile_path=dockerfile_path,
            dockerfile_file=dockerfile_file,
            tags=list(getattr(args, "tags", None) or []),
            build_args=_parse_build_args(
                getattr(args, "build_args", None) or []
            ),
            architecture=getattr(args, "override_arch", None),
            target_stage=getattr(args, "target_stage", None) or None,
            emulator=getattr(args, "emulator", None) or "",
            outputs=outputs,
            install_as=getattr(args, "install_as", None),
            no_cache=bool(getattr(args, "no_cache", False)),
            verbose=bool(getattr(args, "verbose", False)),
            quiet=quiet,
            build_output_fd=getattr(args, "_api_build_output_fd", None),
            ensure_proot=_ensure_proot,
            on_built=(None if quiet else _print_build_summary),
            dockerfile_limit=MAX_DOCKERFILE_BYTES,
            context=OperationContext(
                verbose=bool(getattr(args, "verbose", False))
            ),
        )
    except (AlreadyExistsFailure, BusyFailure, BuildValidationFailure,
            NotFoundFailure) as exc:
        crit_error(str(exc))
        sys.exit(1)
    except OperationFailure as exc:
        if not getattr(exc, "reported", False):
            log_error(str(exc))
        sys.exit(1)
    except KeyboardInterrupt:
        log_error("Aborted by user.")
        sys.exit(1)
    finally:
        if (dockerfile_file is not None
                and getattr(args, "_api_close_dockerfile_file", False)):
            dockerfile_file.close()

    if quiet:
        if result.installed_container is not None:
            _print_install_summary(
                result.installed_container.name,
                result.installed_container.has_entrypoint,
            )
        return
    tags = [image.reference for image in result.images]
    if result.installed_container is not None:
        _print_install_summary(
            result.installed_container.name,
            result.installed_container.has_entrypoint,
        )
    elif not result.outputs:
        msg(f"{C['CYAN']}Install with: "
            f"{C['GREEN']}{PROGRAM_NAME} install {tags[0]}{C['RST']}")
        msg()


def _print_build_summary(tags, layer_count, size_bytes) -> None:
    """Render the CLI build summary before optional installation begins."""
    msg()
    msg(f"{C['CYAN']}Tag(s): "
        f"{C['GREEN']}{', '.join(tags)}{C['RST']}")
    msg(f"{C['CYAN']}Layers: "
        f"{C['GREEN']}{layer_count}"
        f" ({fmt_size(size_bytes)} total){C['RST']}")
    msg()


def _ensure_proot() -> None:
    """Import the CLI probe lazily to avoid its command import cycle."""
    from proot_distro.cli import ensure_proot_installed
    ensure_proot_installed()


def _parse_build_args(raw):
    """Translate repeatable CLI ``--build-arg`` values into a mapping."""
    out = {}
    for item in raw:
        if "=" in item:
            key, _, value = item.partition("=")
        else:
            key, value = item, os.environ.get(item, "")
        if key:
            out[key] = value
    return out


__all__ = (
    "_derive_tag_from_path",
    "_make_build_tmp",
    "_parse_build_args",
    "command_build",
)
