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

# Architecture: Renames a container directory and updates any proot
# link2symlink (l2s) symlinks that point into the old rootfs path.
# Validation reuses _validate_alias() from install.py so that alias
# format rules are enforced in one place.

import os
import sys

from proot_distro.constants import INSTALLED_ROOTFS_DIR
from proot_distro.colors import C, msg
from proot_distro.commands.install import _validate_alias


def command_rename(args, configs: dict) -> None:  # noqa: ARG001
    orig = args.orig_alias
    new = args.new_alias

    if orig == new:
        msg()
        msg(f"{C['BRED']}Error: original and new aliases must differ.{C['RST']}")
        msg()
        sys.exit(1)

    if not _validate_alias(new):
        msg()
        msg(f"{C['BRED']}Error: invalid new alias "
            f"'{C['YELLOW']}{new}{C['BRED']}'. "
            f"Must start with alphanumeric and contain only "
            f"[a-z0-9_.+-].{C['RST']}")
        msg()
        sys.exit(1)

    orig_rootfs = os.path.join(INSTALLED_ROOTFS_DIR, orig)
    new_rootfs = os.path.join(INSTALLED_ROOTFS_DIR, new)

    if not os.path.isdir(orig_rootfs):
        msg()
        msg(f"{C['BRED']}Error: distribution "
            f"'{C['YELLOW']}{orig}{C['BRED']}' is not installed.{C['RST']}")
        msg()
        sys.exit(1)

    if os.path.isdir(new_rootfs):
        msg()
        msg(f"{C['BRED']}Error: rootfs directory for "
            f"'{C['YELLOW']}{new}{C['BRED']}' already exists.{C['RST']}")
        msg()
        sys.exit(1)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Renaming '{orig_rootfs}' to '{new_rootfs}'...{C['RST']}")
    os.rename(orig_rootfs, new_rootfs)

    # Update proot link2symlink (l2s) symlinks that point into the old path.
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Updating PRoot link2symlink extension files "
        f"(may take a long time)...{C['RST']}")
    for dirpath, _dirs, filenames in os.walk(new_rootfs):
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                if os.path.islink(fpath):
                    target = os.readlink(fpath)
                    if target.startswith(orig_rootfs):
                        new_target = new_rootfs + target[len(orig_rootfs):]
                        os.unlink(fpath)
                        os.symlink(new_target, fpath)
            except OSError:
                pass

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Finished renaming the distribution.{C['RST']}")
