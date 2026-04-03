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

from proot_distro.constants import INSTALLED_ROOTFS_DIR
from proot_distro.colors import C, msg


def _resolve_copy_path(spec: str) -> str:
    """Resolve a 'dist:path' or plain path to a real host path."""
    if ":" in spec:
        dist, _, rel_path = spec.partition(":")
        rootfs = os.path.join(INSTALLED_ROOTFS_DIR, dist)
        if not os.path.isdir(rootfs):
            msg()
            msg(f"{C['BRED']}Error: distribution '{C['YELLOW']}{dist}{C['BRED']}' is not installed.{C['RST']}")
            msg()
            sys.exit(1)
        return os.path.normpath(os.path.join(rootfs, rel_path.lstrip("/")))
    return os.path.normpath(os.path.abspath(spec))


def command_copy(args, configs: dict) -> None:
    src  = args.source
    dest = args.destination
    verbose = getattr(args, "verbose", False)
    move_mode = getattr(args, "move", False)

    src_path  = _resolve_copy_path(src)
    dest_path = _resolve_copy_path(dest)

    if not os.path.exists(src_path):
        msg()
        msg(f"{C['BRED']}Error: can't copy '{C['YELLOW']}{src}{C['BRED']}' because file does not exist.{C['RST']}")
        msg()
        sys.exit(1)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Source: '{src_path}'{C['RST']}")
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Destination: '{dest_path}'{C['RST']}")

    dest_dir = os.path.dirname(dest_path)
    if not os.path.isdir(dest_dir):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Creating directory '{dest_dir}'...{C['RST']}")
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as exc:
            msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}Failure.{C['RST']}")
            msg()
            msg(f"{C['BRED']}Error: unable to create directory at '{C['YELLOW']}{dest_dir}{C['BRED']}'.{C['RST']}")
            msg()
            sys.exit(1)

    def _verbose_copy2(src, dst, *, follow_symlinks=True):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Copying: '{src}' -> '{dst}'{C['RST']}")
        return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)

    try:
        if move_mode:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Moving files...{C['RST']}")
            if verbose:
                if os.path.isdir(src_path):
                    for root, _dirs, files in os.walk(src_path):
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            rel = os.path.relpath(fpath, src_path)
                            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Moving: '{fpath}' -> '{os.path.join(dest_path, rel)}'{C['RST']}")
                else:
                    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Moving: '{src_path}' -> '{dest_path}'{C['RST']}")
            shutil.move(src_path, dest_path)
        else:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Copying files, this may take a while...{C['RST']}")
            copy_fn = _verbose_copy2 if verbose else shutil.copy2
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dest_path, symlinks=True,
                                copy_function=copy_fn)
            else:
                if verbose:
                    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Copying: '{src_path}' -> '{dest_path}'{C['RST']}")
                shutil.copy2(src_path, dest_path)
    except KeyboardInterrupt:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}Aborted by user.{C['RST']}")
        sys.exit(1)
    except OSError as exc:
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}Failure.{C['RST']}")
        msg()
        if move_mode:
            msg(f"{C['BRED']}Error: unable to move file into '{C['YELLOW']}{dest_path}{C['BRED']}'.{C['RST']}")
        else:
            msg(f"{C['BRED']}Error: unable to copy file into '{C['YELLOW']}{dest_path}{C['BRED']}'.{C['RST']}")
        msg()
        sys.exit(1)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Finished copying files.{C['RST']}")
