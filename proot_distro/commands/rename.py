"""
Proot-Distro - manage proot containers on Termux.

Created by Sylirre <sylirre@termux.dev> for Termux project.
Development assisted by Claude Code (https://claude.ai/code).

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""
import os
import shutil
import sys

from proot_distro.constants import PD_CONFIGS_DIR, INSTALLED_ROOTFS_DIR
from proot_distro.colors import C, msg
from proot_distro.config import is_bundled_config
from proot_distro.commands.install import _validate_alias


def command_rename(args, configs: dict) -> None:
    orig = args.orig_alias
    new  = args.new_alias

    if orig == new:
        msg()
        msg(f"{C['BRED']}Error: original and new aliases must differ.{C['RST']}")
        msg()
        sys.exit(1)

    if not _validate_alias(new):
        msg()
        msg(f"{C['BRED']}Error: invalid new alias '{C['YELLOW']}{new}{C['BRED']}'. "
            f"Must start with alphanumeric and contain only [a-z0-9_.+-].{C['RST']}")
        msg()
        sys.exit(1)

    if orig not in configs:
        msg()
        msg(f"{C['BRED']}Error: unknown distribution '{C['YELLOW']}{orig}{C['BRED']}' was requested to be renamed.{C['RST']}")
        msg()
        sys.exit(1)

    orig_rootfs = os.path.join(INSTALLED_ROOTFS_DIR, orig)
    new_rootfs  = os.path.join(INSTALLED_ROOTFS_DIR, new)

    if not os.path.isdir(orig_rootfs):
        msg()
        msg(f"{C['BRED']}Error: distribution '{C['YELLOW']}{orig}{C['BRED']}' is not installed.{C['RST']}")
        msg()
        sys.exit(1)

    if os.path.isdir(new_rootfs):
        msg()
        msg(f"{C['BRED']}Error: rootfs directory for '{C['YELLOW']}{new}{C['BRED']}' already exists.{C['RST']}")
        msg()
        sys.exit(1)

    if new in configs:
        msg()
        msg(f"{C['BRED']}Error: distribution with alias '{C['YELLOW']}{new}{C['BRED']}' already exists.{C['RST']}")
        msg()
        sys.exit(1)

    orig_cfg = configs[orig]
    new_cfg_path = os.path.join(PD_CONFIGS_DIR, new + ".yaml")

    if is_bundled_config(orig):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Copying '{orig_cfg.config_path}' to '{new_cfg_path}'...{C['RST']}")
        shutil.copy2(orig_cfg.config_path, new_cfg_path)
    else:
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Renaming '{orig_cfg.config_path}' to '{new_cfg_path}'...{C['RST']}")
        shutil.copy2(orig_cfg.config_path, new_cfg_path)
        os.remove(orig_cfg.config_path)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Renaming '{orig_rootfs}' to '{new_rootfs}'...{C['RST']}")
    os.rename(orig_rootfs, new_rootfs)

    # Update proot link2symlink (l2s) symlinks that point into the old rootfs path.
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Updating PRoot link2symlink extension files (may take long time)...{C['RST']}")
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

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Finished renaming the distribution.{C['RST']}")
