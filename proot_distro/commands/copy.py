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
import stat
import sys
from contextlib import ExitStack

from proot_distro.message import log_info, log_error, crit_error
from proot_distro.paths import (
    container_locks_for_spec_pair,
    open_pinned_leaf,
    pin_path,
    resolve_container_path,
    warn_unpinned,
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


def _copy_file_nofollow(src_io, dest_pin):
    """Copy a single file, refusing to write through a symlinked leaf.

    shutil.copy2() opens the destination by name and follows a symlink
    sitting there. Between resolving the destination and this write, a
    process inside the container can plant one; opening with O_NOFOLLOW
    ourselves is what keeps the bytes inside the rootfs. Metadata is
    then copied through /proc/self/fd so copy2's semantics (mode,
    timestamps, xattrs) survive, with a plain fchmod/utime fallback for
    hosts without /proc.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    fd = open_pinned_leaf(dest_pin, flags)
    try:
        with open(src_io, "rb") as fin, open(fd, "wb", closefd=False) as fout:
            shutil.copyfileobj(fin, fout)
        try:
            shutil.copystat(src_io, f"/proc/self/fd/{fd}")
        except OSError:
            src_st = os.stat(src_io)
            try:
                os.fchmod(fd, stat.S_IMODE(src_st.st_mode))
                os.utime(fd, ns=(src_st.st_atime_ns, src_st.st_mtime_ns))
            except OSError:
                pass
    finally:
        os.close(fd)


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

    src_is_dir = os.path.isdir(src_path)
    if src_is_dir and not recursive and not move_mode:
        crit_error(f"source path is a directory. Use option '--recursive' "
                   f"to copy directories.")
        sys.exit(1)

    # A file copied onto an existing directory lands inside it, keeping
    # its own name. shutil does this implicitly; spell it out so the
    # destination names the file we are about to open with O_NOFOLLOW.
    if not src_is_dir and os.path.isdir(dest_path):
        dest_path = os.path.join(dest_path, os.path.basename(src_path))

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

    warn_unpinned(src)
    warn_unpinned(dest)

    def _verbose_copy2(s, d, *, follow_symlinks=True):
        log_info(f"Copying: '{s}' -> '{d}'")
        return shutil.copy2(s, d, follow_symlinks=follow_symlinks)

    # Pin both endpoints for the duration of the transfer: from here on
    # the filesystem is addressed through the pinned fds, so renaming a
    # directory component to a symlink no longer redirects the copy.
    try:
        with ExitStack() as pins:
            src_pin = pins.enter_context(pin_path(src, src_path))
            dest_pin = pins.enter_context(pin_path(dest, dest_path))
            src_io, dest_io = src_pin.io, dest_pin.io

            if move_mode:
                log_info("Moving files...")
                if verbose:
                    if src_is_dir:
                        for root, _dirs, files in os.walk(src_io):
                            for fname in files:
                                fpath = os.path.join(root, fname)
                                rel = os.path.relpath(fpath, src_io)
                                log_info(
                                    f"Moving: '{os.path.join(src_path, rel)}'"
                                    f" -> '{os.path.join(dest_path, rel)}'"
                                )
                    else:
                        log_info(f"Moving: '{src_path}' -> '{dest_path}'")
                # rename(2) replaces a symlink at the destination instead
                # of following it, so move needs no O_NOFOLLOW handling.
                shutil.move(src_io, dest_io)
            else:
                log_info("Copying files, this may take a while...")
                if src_is_dir:
                    copy_fn = _verbose_copy2 if verbose else shutil.copy2
                    shutil.copytree(src_io, dest_io, symlinks=True,
                                    copy_function=copy_fn)
                else:
                    if verbose:
                        log_info(f"Copying: '{src_path}' -> '{dest_path}'")
                    _copy_file_nofollow(src_io, dest_pin)
    except KeyboardInterrupt:
        clear_bar()
        log_error("Aborted by user.")
        sys.exit(1)
    except OSError as exc:
        log_error(f"Error: {exc}")
        sys.exit(1)

    log_info("Finished copying files.")
