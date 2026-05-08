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

# Architecture: Handles pulling a Docker/OCI image and setting up a new
# proot container. Image references are resolved against Docker Hub or a
# custom registry. All network and filesystem work is delegated to helpers;
# this module only owns the argument validation and top-level install flow.

import os
import re
import shutil
import sys
import tarfile

from proot_distro.constants import (
    DOWNLOAD_CACHE_DIR,
    INSTALLED_ROOTFS_DIR,
    PROGRAM_NAME,
)
from proot_distro.colors import C, msg
from proot_distro.arch import get_device_cpu_arch
from proot_distro.sysdata import setup_fake_sysdata
from proot_distro.helpers.docker import pull_image, derive_alias
from proot_distro.helpers.rootfs import (
    write_environment,
    fix_path_in_configs,
    write_resolv_conf,
    write_hosts,
    register_android_ids,
)

_ALIAS_RE = re.compile(r'^[a-z0-9][a-z0-9_.+\-]*$')


def _validate_alias(alias: str) -> bool:
    return bool(_ALIAS_RE.match(alias))


def command_install(args, configs: dict) -> None:  # noqa: ARG001
    """Install a distribution by pulling it from a Docker/OCI registry."""
    image_ref = args.alias
    custom_dist_name = getattr(args, "custom_dist_name", None)

    if custom_dist_name is not None and not custom_dist_name:
        msg()
        msg(f"{C['BRED']}Error: distribution name can't be empty.{C['RST']}")
        msg()
        sys.exit(1)

    install_name = custom_dist_name if custom_dist_name else derive_alias(image_ref)

    if custom_dist_name and not _validate_alias(custom_dist_name):
        msg()
        msg(f"{C['BRED']}Error: invalid alias "
            f"'{C['YELLOW']}{custom_dist_name}{C['BRED']}'. "
            f"Must start with alphanumeric and contain only "
            f"[a-z0-9_.+-].{C['RST']}")
        msg()
        sys.exit(1)

    rootfs_dir = os.path.join(INSTALLED_ROOTFS_DIR, install_name)
    if os.path.isdir(rootfs_dir):
        msg()
        msg(f"{C['BRED']}Error: distribution "
            f"'{C['YELLOW']}{install_name}{C['BRED']}' is already "
            f"installed.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Log in:     "
            f"{C['GREEN']}{PROGRAM_NAME} login {install_name}{C['RST']}")
        msg(f"{C['CYAN']}Reinstall:  "
            f"{C['GREEN']}{PROGRAM_NAME} reset {install_name}{C['RST']}")
        msg(f"{C['CYAN']}Uninstall:  "
            f"{C['GREEN']}{PROGRAM_NAME} remove {install_name}{C['RST']}")
        msg()
        sys.exit(1)

    device_arch = get_device_cpu_arch()
    dist_arch = getattr(args, "override_arch", None) or device_arch

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Installing "
        f"'{C['YELLOW']}{image_ref}{C['CYAN']}' as "
        f"'{C['YELLOW']}{install_name}{C['CYAN']}'...{C['RST']}")

    os.makedirs(rootfs_dir, exist_ok=True)

    def _cleanup() -> None:
        try:
            shutil.rmtree(rootfs_dir)
        except OSError:
            pass

    try:
        os.makedirs(DOWNLOAD_CACHE_DIR, exist_ok=True)

        metadata = pull_image(image_ref, rootfs_dir, dist_arch)

        if not os.path.isdir(os.path.join(rootfs_dir, "etc")):
            msg()
            msg(f"{C['BRED']}Error: extracted rootfs has no /etc directory. "
                f"The image may be incompatible with proot.{C['RST']}")
            msg()
            _cleanup()
            sys.exit(1)

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Writing file '{rootfs_dir}/etc/environment'...{C['RST']}")
        write_environment(rootfs_dir)
        fix_path_in_configs(rootfs_dir)

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Creating file '{rootfs_dir}/etc/resolv.conf'...{C['RST']}")
        write_resolv_conf(rootfs_dir)

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Creating file '{rootfs_dir}/etc/hosts'...{C['RST']}")
        write_hosts(rootfs_dir)

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Registering Android-specific UIDs and GIDs...{C['RST']}")
        register_android_ids(rootfs_dir)

        setup_fake_sysdata(install_name)

        image_env = metadata.get("env", [])
        if image_env:
            meta_dir = os.path.join(rootfs_dir, ".proot-distro")
            os.makedirs(meta_dir, exist_ok=True)
            with open(os.path.join(meta_dir, "image-env"), "w") as fh:
                fh.write("\n".join(image_env) + "\n")

    except KeyboardInterrupt:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Aborted by user.{C['RST']}")
        _cleanup()
        sys.exit(1)
    except (EOFError, OSError, tarfile.TarError, RuntimeError) as exc:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Failed to install: {exc}{C['RST']}")
        msg()
        _cleanup()
        sys.exit(1)
    except Exception:
        _cleanup()
        raise

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Finished installation.{C['RST']}")
    msg()
    msg(f"{C['CYAN']}Log in with: "
        f"{C['GREEN']}{PROGRAM_NAME} login {install_name}{C['CYAN']}{C['RST']}")
    msg()
