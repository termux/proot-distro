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

# Architecture: Top-level orchestration for `proot-distro push`. The
# image must have been produced by `proot-distro build -t IMAGE` first;
# the manifest lives in MANIFEST_CACHE_DIR and the layer + config blobs
# live in LAYER_CACHE_DIR. push_image() uploads them all to the
# registry and PUTs the manifest under the supplied tag. Authentication
# uses the same PD_DOCKER_AUTH=user:password contract as install, and
# is optional for self-hosted registries that allow anonymous push.

import sys
import urllib.error

from proot_distro.message import C, msg, log_info, log_error, crit_error
from proot_distro.locking import BuildLock
from proot_distro.arch import get_device_cpu_arch, normalize_arch
from proot_distro.helpers.docker import (
    load_manifest_cache,
    parse_image_ref,
    push_image,
)
from proot_distro.progress import fmt_size
from proot_distro.constants import PROGRAM_NAME


def command_push(args):
    """Implements `proot-distro push`."""
    image_ref = getattr(args, "image_ref", None) or ""
    override_arch = getattr(args, "override_arch", None) or ""
    quiet = bool(getattr(args, "quiet", False))

    if not image_ref:
        crit_error("image reference is not specified (e.g. 'myrepo/myapp:1.0').")
        sys.exit(1)

    # Append :latest the same way build does, so users can push using the
    # short form even when they tagged the build with the implicit tag.
    last = image_ref.split("/")[-1]
    if ":" not in last:
        image_ref = image_ref + ":latest"

    if override_arch:
        target_arch = normalize_arch(override_arch)
        if target_arch is None:
            crit_error(f"unknown architecture '{override_arch}'.")
            sys.exit(1)
    else:
        target_arch = get_device_cpu_arch()

    # Pre-flight check: refuse early when no manifest is cached for this
    # image_ref + arch. This catches a typoed tag before we open a
    # network connection.
    manifest, _, _ = load_manifest_cache(image_ref, target_arch)
    if manifest is None:
        crit_error(
            f"No image found in local cache for "
            f"'{image_ref}' ({target_arch}). "
            f"Build it first with: {PROGRAM_NAME} build -t {image_ref}"
        )
        sys.exit(1)

    registry, _, _ = parse_image_ref(image_ref)
    display_registry = registry or "docker.io"

    if not quiet:
        log_info(f"Pushing '{image_ref}' ({target_arch}) "
                 f"to '{display_registry}'...")

    try:
        with BuildLock(image_ref, target_arch, command="push"):
            result = push_image(image_ref, target_arch)
    except KeyboardInterrupt:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        log_error("Aborted by user.")
        sys.exit(1)
    except (urllib.error.URLError, OSError) as exc:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        log_error(f"Network error: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        log_error(f"Error: {exc}")
        sys.exit(1)

    if quiet:
        return

    log_info("Push complete.")
    msg()
    msg(f"{C['CYAN']}Repository: "
        f"{C['GREEN']}{result['registry']}/{result['repo']}{C['RST']}")
    msg(f"{C['CYAN']}Tag:        "
        f"{C['GREEN']}{result['tag']}{C['RST']}")
    if result.get("manifest_digest"):
        msg(f"{C['CYAN']}Digest:     "
            f"{C['GREEN']}{result['manifest_digest']}{C['RST']}")
    msg(f"{C['CYAN']}Uploaded:   "
        f"{C['GREEN']}{fmt_size(result['bytes_uploaded'])}{C['RST']}")
    msg()
