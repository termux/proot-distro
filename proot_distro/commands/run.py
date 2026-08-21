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
#
# What that block *says* is nobody's promise. It is a registry's JSON,
# persisted verbatim by install, and on Termux the file holding it sits
# under the $TERMUX_PREFIX bound read-write into every non-isolated
# container. So the three fields this command reads are checked against
# the shape OCI gives them before anything is built out of them:
# Entrypoint and Cmd are lists of strings and WorkingDir is a string, or
# the command refuses. `list(cfg.get("Entrypoint") or [])` accepted far
# more than that -- an int ended the command in a TypeError traceback, a
# JSON object yielded its keys, and the string "sh" became
# ['s', 'h'], which is an argv nobody wrote. A list holding a non-string
# survived this module entirely and surfaced as a TypeError out of
# os.execvpe(), past every net.

import sys

from proot_distro.message import crit_error, quote_error
from proot_distro.commands.login import command_login
from proot_distro.names import require_valid_name
from proot_distro.paths import (
    container_is_installed, manifest_image_config, read_container_manifest,
)


def _read_image_config(container_name: str) -> dict:
    """Return the image_config.config dict from manifest.json, or {}.

    Read through the container directory's own descriptor: what `run`
    executes comes out of this file, so a symlink left under the name
    would decide it, and a FIFO would hang the command outright (see
    paths.open_container_manifest).
    """
    try:
        data = read_container_manifest(container_name)
    except FileNotFoundError:
        crit_error(f"no image manifest found for container '{container_name}' "
                   f"which is required for command 'run'.")
        sys.exit(1)
    except (OSError, ValueError) as exc:
        crit_error(f"cannot read manifest.json for '{container_name}': "
                   f"{quote_error(exc)}")
        sys.exit(1)
    return manifest_image_config(data)


def _string_list(img_cfg: dict, key: str, container_name: str) -> list:
    """The image config's *key* as a list of strings, or exit.

    A missing (or JSON null) field is "not set", which is how an image
    says it has no Entrypoint or no Cmd. Anything present but shaped
    otherwise is a malformed image and is reported as one: quietly
    dropping it would run a *different* command than the image names,
    which is worse than refusing, and quietly coercing it invents an
    argv out of characters or dict keys.
    """
    value = img_cfg.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        crit_error(f"the image manifest for '{container_name}' declares a "
                   f"{key} that is not a list of strings; the image is "
                   f"malformed.")
        sys.exit(1)
    return list(value)


def _working_dir(img_cfg: dict, container_name: str) -> str:
    """The image config's WorkingDir, or "/". Exits for a non-string.

    It becomes proot's --cwd, so a value of another type used to reach
    the argv through an f-string and name a directory no image meant.
    """
    value = img_cfg.get("WorkingDir")
    if value is None:
        return "/"
    if not isinstance(value, str):
        crit_error(f"the image manifest for '{container_name}' declares a "
                   f"WorkingDir that is not a string; the image is "
                   f"malformed.")
        sys.exit(1)
    return value or "/"


def command_run(args) -> None:
    """Execute the container image's Entrypoint/Cmd inside proot."""
    container_name = args.container_name
    run_args = getattr(args, "run_args", []) or []

    require_valid_name(container_name)

    if not container_is_installed(container_name):
        crit_error(f"container '{container_name}' is not installed.")
        sys.exit(1)

    img_cfg = _read_image_config(container_name)

    entrypoint = _string_list(img_cfg, "Entrypoint", container_name)
    cmd = _string_list(img_cfg, "Cmd", container_name)

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
        args.work_dir = _working_dir(img_cfg, container_name)

    # Signal to command_login to bypass shell wrapping and run inner directly.
    args._run_inner = inner
    args.login_cmd = []
    command_login(args)
