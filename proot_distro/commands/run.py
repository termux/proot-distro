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

# Architecture: Runs the Entrypoint and/or Cmd defined in a container's
# Docker image manifest (containers/<name>/manifest.json). Reads the
# image_config.config block written by install, builds the inner command
# list, then delegates entirely to command_login with the pre-built inner
# command injected via args._run_inner so that login's proot setup is
# reused without duplication.

import json
import os
import sys

from proot_distro.message import crit_error
from proot_distro.commands.login import command_login
from proot_distro.names import require_valid_name
from proot_distro.paths import container_manifest, container_rootfs


def _read_image_config(container_name: str) -> dict:
    """Return the image_config.config dict from manifest.json, or {}."""
    manifest_path = container_manifest(container_name)
    try:
        with open(manifest_path) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        crit_error(f"no image manifest found for container '{container_name}' "
                   f"which is required for command 'run'.")
        sys.exit(1)
    except (OSError, json.JSONDecodeError) as exc:
        crit_error(f"cannot read manifest.json for '{container_name}': {exc}")
        sys.exit(1)
    return data.get("image_config", {}).get("config") or {}


def command_run(args) -> None:
    """Execute the container image's Entrypoint/Cmd inside proot."""
    container_name = args.container_name
    run_args = getattr(args, "run_args", []) or []

    require_valid_name(container_name)

    rootfs = container_rootfs(container_name)
    if not os.path.isdir(rootfs):
        crit_error(f"container '{container_name}' is not installed.")
        sys.exit(1)

    img_cfg = _read_image_config(container_name)

    entrypoint: list = list(img_cfg.get("Entrypoint") or [])
    cmd: list = list(img_cfg.get("Cmd") or [])

    if run_args:
        # Args after '--' replace Cmd but are appended to Entrypoint.
        inner = entrypoint + run_args
    elif entrypoint or cmd:
        inner = entrypoint + cmd
    else:
        crit_error(f"the image manifest for '{container_name}' defines neither "
                   f"Entrypoint nor Cmd, and no command was given after "
                   f"'--'.")
        sys.exit(1)

    if not inner:
        crit_error(f"resolved command is empty for container "
                   f"'{container_name}'.")
        sys.exit(1)

    # Use WorkingDir from image config unless --work-dir was given.
    # Fall back to "/" when neither is available.
    if not getattr(args, "work_dir", None):
        args.work_dir = img_cfg.get("WorkingDir") or "/"

    # Signal to command_login to bypass shell wrapping and run inner directly.
    args._run_inner = inner
    args.login_cmd = []
    command_login(args)
