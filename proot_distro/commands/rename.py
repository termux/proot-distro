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

# Architecture: Renames a container directory (containers/<old> to
# containers/<new>) and updates any proot link2symlink (l2s) symlinks
# that point into the old rootfs path. Validation reuses _validate_name()
# from install.py so that name format rules are enforced in one place.

import os
import signal
import sys

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.colors import C, msg
from proot_distro.commands.install import _validate_name
from proot_distro.locking import ContainerLock


def command_rename(args, configs: dict) -> None:  # noqa: ARG001
    orig = args.orig_alias
    new = args.new_alias

    if orig == new:
        msg()
        msg(f"{C['BRED']}Error: original and new names must differ.{C['RST']}")
        msg()
        sys.exit(1)

    if not _validate_name(orig):
        msg()
        msg(f"{C['BRED']}Error: invalid original name "
            f"'{C['YELLOW']}{orig}{C['BRED']}'. "
            f"Must start with alphanumeric and contain only "
            f"letters, digits, underscores, dots, or hyphens.{C['RST']}")
        msg()
        sys.exit(1)

    if not _validate_name(new):
        msg()
        msg(f"{C['BRED']}Error: invalid new name "
            f"'{C['YELLOW']}{new}{C['BRED']}'. "
            f"Must start with alphanumeric and contain only "
            f"letters, digits, underscores, dots, or hyphens.{C['RST']}")
        msg()
        sys.exit(1)

    orig_dir = os.path.join(CONTAINERS_DIR, orig)
    new_dir = os.path.join(CONTAINERS_DIR, new)
    orig_rootfs = os.path.join(orig_dir, "rootfs")
    new_rootfs = os.path.join(new_dir, "rootfs")

    if not os.path.isdir(orig_rootfs):
        msg()
        msg(f"{C['BRED']}Error: container "
            f"'{C['YELLOW']}{orig}{C['BRED']}' is not installed.{C['RST']}")
        msg()
        sys.exit(1)

    if os.path.isdir(new_dir):
        msg()
        msg(f"{C['BRED']}Error: container "
            f"'{C['YELLOW']}{new}{C['BRED']}' already exists.{C['RST']}")
        msg()
        sys.exit(1)

    # Acquire locks in sorted order to ensure consistent ordering.
    first, second = (orig, new) if orig < new else (new, orig)
    with ContainerLock(first, exclusive=True, command="rename"):
        with ContainerLock(second, exclusive=True, command="rename"):
            _do_rename(orig, new, orig_dir, new_dir, new_rootfs, orig_rootfs)


def _do_rename(orig, new, orig_dir, new_dir, new_rootfs, orig_rootfs):
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Renaming '{orig}' to '{new}'...{C['RST']}")
    try:
        os.rename(orig_dir, new_dir)
    except OSError as exc:
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Failed to rename container: {exc}{C['RST']}")
        sys.exit(1)

    # Update proot link2symlink (l2s) symlinks that point into the old path.
    # SIGINT is deferred for this block: an interrupt mid-rewrite would leave
    # some symlinks pointing at the old (now-gone) path, breaking the container.
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Updating PRoot link2symlink extension files "
        f"(may take a long time)...{C['RST']}")
    def _warn_no_interrupt(_signum, _frame):
        msg(f"\n{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['RED']}"
            f"Terminating now will leave link2symlink symlinks broken. "
            f"Please wait for the operation to complete.{C['RST']}")

    _old_sigint = signal.signal(signal.SIGINT, _warn_no_interrupt)
    _old_sigquit = signal.signal(signal.SIGQUIT, _warn_no_interrupt)
    try:
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
    finally:
        signal.signal(signal.SIGINT, _old_sigint)
        signal.signal(signal.SIGQUIT, _old_sigquit)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Finished renaming the container.{C['RST']}")
