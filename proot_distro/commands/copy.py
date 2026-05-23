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

# Architecture: Copies or moves files between the host filesystem and paths
# inside installed proot containers. Source/destination are specified with
# an optional 'container:path' prefix. Recursive mode copies entire directory
# trees preserving symlinks (like cp -a).

import os
import shutil
import sys
from contextlib import ExitStack

from proot_distro.message import log_info, log_error, crit_error
from proot_distro.paths import (
    container_locks_for_spec_pair,
    resolve_container_path,
)
from proot_distro.progress import clear_bar


def command_copy(args) -> None:
    """Copy or move files between host paths and container paths."""
    src = args.source
    dest = args.destination
    verbose = getattr(args, "verbose", False)
    move_mode = getattr(args, "move", False)
    recursive = getattr(args, "recursive", False)

    with ExitStack() as stack:
        for lock in container_locks_for_spec_pair(src, dest, command="copy"):
            stack.enter_context(lock)
        _do_copy(src, dest, verbose, move_mode, recursive)


def _do_copy(src, dest, verbose, move_mode, recursive):
    src_path = resolve_container_path(src)
    dest_path = resolve_container_path(dest)

    # Reject '.' or '..' as destination component (but allow as source).
    dest_base = os.path.basename(dest_path)
    if dest_base in (".", ".."):
        crit_error("paths '.' and '..' are not allowed as copy destination.")
        sys.exit(1)

    if not os.path.exists(src_path):
        crit_error(f"cannot copy '{src}' because the path does not exist.")
        sys.exit(1)

    if not os.access(src_path, os.R_OK):
        crit_error(f"source path '{src_path}' is not readable.")
        sys.exit(1)

    if os.path.isdir(src_path) and not recursive and not move_mode:
        crit_error(f"source path is a directory. Use option '--recursive' "
                   f"to copy directories.")
        sys.exit(1)

    log_info(f"Source: '{src_path}'")
    log_info(f"Destination: '{dest_path}'")

    dest_dir = os.path.dirname(dest_path)
    if not os.path.isdir(dest_dir):
        log_info(f"Creating directory '{dest_dir}'...")
        try:
            os.makedirs(dest_dir, exist_ok=True)
        except OSError as exc:
            log_error(f"Cannot create directory '{dest_dir}': {exc}")
            sys.exit(1)

    def _verbose_copy2(src, dst, *, follow_symlinks=True):
        log_info(f"Copying: '{src}' -> '{dst}'")
        return shutil.copy2(src, dst, follow_symlinks=follow_symlinks)

    try:
        if move_mode:
            log_info("Moving files...")
            if verbose:
                if os.path.isdir(src_path):
                    for root, _dirs, files in os.walk(src_path):
                        for fname in files:
                            fpath = os.path.join(root, fname)
                            rel = os.path.relpath(fpath, src_path)
                            log_info(
                                f"Moving: '{fpath}' -> "
                                f"'{os.path.join(dest_path, rel)}'"
                            )
                else:
                    log_info(f"Moving: '{src_path}' -> '{dest_path}'")
            shutil.move(src_path, dest_path)
        else:
            log_info("Copying files, this may take a while...")
            copy_fn = _verbose_copy2 if verbose else shutil.copy2
            if os.path.isdir(src_path):
                shutil.copytree(src_path, dest_path, symlinks=True,
                                copy_function=copy_fn)
            else:
                if verbose:
                    log_info(f"Copying: '{src_path}' -> '{dest_path}'")
                shutil.copy2(src_path, dest_path)
    except KeyboardInterrupt:
        clear_bar()
        log_error("Aborted by user.")
        sys.exit(1)
    except OSError as exc:
        log_error(f"Error: {exc}")
        sys.exit(1)

    log_info("Finished copying files.")
