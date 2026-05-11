#
# Proot-Distro - manage proot containers on Termux.
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

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.colors import C, msg
from proot_distro.commands.login import command_login


def _read_image_config(dist_name: str) -> dict:
    """Return the image_config.config dict from manifest.json, or {}."""
    manifest_path = os.path.join(CONTAINERS_DIR, dist_name, "manifest.json")
    try:
        with open(manifest_path) as fh:
            data = json.load(fh)
    except FileNotFoundError:
        msg()
        msg(f"{C['BRED']}Error: no image manifest found for container "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}'. "
            f"Container may have been installed before manifest tracking\n"
            f"was introduced. Use "
            f"'{C['YELLOW']}reset{C['BRED']}' to reinstall it.{C['RST']}")
        msg()
        sys.exit(1)
    except (OSError, json.JSONDecodeError) as exc:
        msg()
        msg(f"{C['BRED']}Error: cannot read manifest.json for "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}': {exc}{C['RST']}")
        msg()
        sys.exit(1)
    return data.get("image_config", {}).get("config") or {}


def command_run(args, configs: dict) -> None:
    dist_name = args.alias
    run_args = getattr(args, "run_args", []) or []

    img_cfg = _read_image_config(dist_name)

    entrypoint: list = list(img_cfg.get("Entrypoint") or [])
    cmd: list = list(img_cfg.get("Cmd") or [])

    if run_args:
        # Args after '--' replace Cmd but are appended to Entrypoint.
        inner = entrypoint + run_args
    elif entrypoint or cmd:
        inner = entrypoint + cmd
    else:
        msg()
        msg(f"{C['BRED']}Error: the image manifest for "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}' defines neither "
            f"Entrypoint nor Cmd, and no command was given after "
            f"'--'.{C['RST']}")
        msg()
        sys.exit(1)

    if not inner:
        msg()
        msg(f"{C['BRED']}Error: resolved command is empty for container "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}'.{C['RST']}")
        msg()
        sys.exit(1)

    # Use WorkingDir from image config unless --work-dir was given.
    # Fall back to "/" when neither is available.
    if not getattr(args, "work_dir", None):
        args.work_dir = img_cfg.get("WorkingDir") or "/"

    if getattr(args, "debug", False):
        if entrypoint:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Entrypoint: {json.dumps(entrypoint)}{C['RST']}")
        if cmd:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Cmd: {json.dumps(cmd)}{C['RST']}")

    # Signal to command_login to bypass shell wrapping and run inner directly.
    args._run_inner = inner
    args.login_cmd = []
    command_login(args, configs)
