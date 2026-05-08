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

# Architecture: Copies or moves files between the host filesystem and paths
# inside installed proot containers. Source/destination are specified with
# an optional 'container:path' prefix. Recursive mode copies entire directory
# trees preserving symlinks (like cp -a).

import os
import shutil
import sys

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.colors import C, msg


def _resolve_copy_path(spec: str) -> str:
    """Resolve a 'dist:path' or plain path to a real host path."""
    if ":" in spec:
        dist, _, rel_path = spec.partition(":")
        rootfs = os.path.join(CONTAINERS_DIR, dist, "rootfs")
        if not os.path.isdir(rootfs):
            msg()
            msg(f"{C['BRED']}Error: distribution "
                f"'{C['YELLOW']}{dist}{C['BRED']}' is not installed.{C['RST']}")
            msg()
            sys.exit(1)
        return os.path.normpath(os.path.join(rootfs, rel_path.lstrip("/")))
    return os.path.normpath(os.path.abspath(spec))


def command_copy(args, configs: dict) -> None:
    src = args.source
    dest = args.destination
    verbose = getattr(args, "verbose", False)
    move_mode = getattr(args, "move", False)
    recursive = getattr(args, "recursive", False)

    src_path = _resolve_copy_path(src)
    dest_path = _resolve_copy_path(dest)

    # Reject '.' or '..' as destination component (but allow as source).
    dest_base = os.path.basename(dest_path)
    if dest_base in (".", ".."):
        msg()
        msg(f"{C['BRED']}Error: '.' and '..' are not allowed as copy "
            f"destination.{C['RST']}")
        msg()
        sys.exit(1)

    if not os.path.exists(src_path):
        msg()
        msg(f"{C['BRED']}Error: cannot copy "
            f"'{C['YELLOW']}{src}{C['BRED']}' because the path does not "
            f"exist.{C['RST']}")
        msg()
        sys.exit(1)

    if not os.access(src_path, os.R_OK):
        msg()
        msg(f"{C['BRED']}Error: source "
            f"'{C['YELLOW']}{src_path}{C['BRED']}' is not readable.{C['RST']}")
        msg()
        sys.exit(1)

    if os.path.isdir(src_path) and not recursive and not move_mode:
        msg()
        msg(f"{C['BRED']}Error: source is a directory. Use "
            f"'{C['YELLOW']}--recursive{C['BRED']}' to copy "
            f"directories.{C['RST']}")
        msg()
        sys.exit(1)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Source: '{src_path}'{C['RST']}")
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Destination: '{dest_path}'{C['RST']}")

    dest_dir = os.path.dirname(dest_path)
    if not os.path.isdir(dest_dir):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Creating directory '{dest_dir}'...{C['RST']}")
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError:
            msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
                f"Failure.{C['RST']}")
            msg()
            msg(f"{C['BRED']}Error: unable to create directory at "
                f"'{C['YELLOW']}{dest_dir}{C['BRED']}'.{C['RST']}")
            msg()
            sys.exit(1)

    def _verbose_copy2(src, dst, *, follow_symlinks=True):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Copying: '{src}' -> '{dst}'{C['RST']}")
        return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)

    try:
        if move_mode:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Moving files...{C['RST']}")
            if verbose:
                if os.path.isdir(src_path):
                    for root, _dirs, files in os.walk(src_path):
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            rel = os.path.relpath(fpath, src_path)
                            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                                f"{C['CYAN']}Moving: '{fpath}' -> "
                                f"'{os.path.join(dest_path, rel)}'{C['RST']}")
                else:
                    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                        f"Moving: '{src_path}' -> '{dest_path}'{C['RST']}")
            shutil.move(src_path, dest_path)
        else:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Copying files, this may take a while...{C['RST']}")
            copy_fn = _verbose_copy2 if verbose else shutil.copy2
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dest_path, symlinks=True,
                                copy_function=copy_fn)
            else:
                if verbose:
                    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                        f"Copying: '{src_path}' -> '{dest_path}'{C['RST']}")
                shutil.copy2(src_path, dest_path)
    except KeyboardInterrupt:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Aborted by user.{C['RST']}")
        sys.exit(1)
    except OSError:
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Failure.{C['RST']}")
        msg()
        if move_mode:
            msg(f"{C['BRED']}Error: unable to move file into "
                f"'{C['YELLOW']}{dest_path}{C['BRED']}'.{C['RST']}")
        else:
            msg(f"{C['BRED']}Error: unable to copy file into "
                f"'{C['YELLOW']}{dest_path}{C['BRED']}'.{C['RST']}")
        msg()
        sys.exit(1)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Finished copying files.{C['RST']}")
