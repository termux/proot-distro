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

import errno
import os
import stat
import sys
from contextlib import ExitStack

from proot_distro import dirfd
from proot_distro.message import log_info, log_error, crit_error, warn
from proot_distro.paths import (
    container_locks_for_spec_pair,
    pin_path,
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


def _opendir_pinned(pin):
    """Open the directory a pin designates as a readable fd."""
    return dirfd.reopen(pin.dir_fd, pin.leaf)


def _copy_tree_pinned(src_pin, dest_pin, verbose, dest_display):
    """Recreate the source directory under the destination, fd by fd.

    Replaces shutil.copytree(symlinks=True). copytree walks by path, so
    every directory it creates and descends into is addressed by name and
    can be swapped for a symlink mid-transfer; carrying the fds down the
    recursion removes that entirely.
    """
    src_fd = _opendir_pinned(src_pin)
    try:
        src_st = os.fstat(src_fd)
        # mkdirat refuses to create over anything that already exists,
        # including a planted symlink, which is copytree's behaviour too.
        try:
            os.mkdir(dest_pin.leaf, stat.S_IMODE(src_st.st_mode),
                     dir_fd=dest_pin.dir_fd)
        except OSError as exc:
            # The fd-relative call only knows the leaf; report the path.
            raise OSError(exc.errno, exc.strerror, dest_display) from None
        dst_fd = dirfd.opendir_at(dest_pin.dir_fd, dest_pin.leaf)
        try:
            def on_entry(rel):
                if verbose:
                    log_info(f"Copying: '{os.path.join(dest_display, rel)}'")

            def on_skip(rel):
                warn(f"skipping special file '{os.path.join(dest_display, rel)}'.")

            dirfd.copy_tree_at(src_fd, dst_fd,
                               on_entry=on_entry, on_skip=on_skip)
            dirfd.copy_metadata(src_fd, dst_fd, src_st)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def _move_pinned(src_pin, dest_pin, src_is_dir):
    """Move via renameat, falling back to copy+remove across devices.

    rename(2) replaces a symlink sitting at the destination rather than
    following it, and both ends are named relative to a pinned fd, so the
    fast path needs no further protection.
    """
    try:
        os.rename(src_pin.leaf, dest_pin.leaf,
                  src_dir_fd=src_pin.dir_fd, dst_dir_fd=dest_pin.dir_fd)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    if src_is_dir:
        _copy_tree_pinned(src_pin, dest_pin, False, str(dest_pin))
        dirfd.rmtree_at(src_pin.dir_fd, src_pin.leaf, force=True)
    else:
        dirfd.copy_file_at(src_pin.dir_fd, src_pin.leaf,
                           dest_pin.dir_fd, dest_pin.leaf)
        os.unlink(src_pin.leaf, dir_fd=src_pin.dir_fd)


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

    # A file copied onto an existing directory lands inside it, and so
    # does a moved directory. shutil did this implicitly; spell it out so
    # the destination names the entry we are about to create.
    if (not src_is_dir or move_mode) and os.path.isdir(dest_path):
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

    # Pin both endpoints, then address the filesystem only through the
    # pinned fds: neither the endpoints nor anything the walk creates
    # below them can be redirected by a symlink appearing mid-transfer.
    try:
        with ExitStack() as pins:
            src_pin = pins.enter_context(pin_path(src, src_path))
            dest_pin = pins.enter_context(pin_path(dest, dest_path))

            if move_mode:
                log_info("Moving files...")
                if verbose:
                    log_info(f"Moving: '{src_path}' -> '{dest_path}'")
                _move_pinned(src_pin, dest_pin, src_is_dir)
            else:
                log_info("Copying files, this may take a while...")
                if src_is_dir:
                    _copy_tree_pinned(src_pin, dest_pin, verbose, dest_path)
                else:
                    if verbose:
                        log_info(f"Copying: '{src_path}' -> '{dest_path}'")
                    dirfd.copy_file_at(src_pin.dir_fd, src_pin.leaf,
                                       dest_pin.dir_fd, dest_pin.leaf)
    except KeyboardInterrupt:
        clear_bar()
        log_error("Aborted by user.")
        sys.exit(1)
    except OSError as exc:
        log_error(f"Error: {exc}")
        sys.exit(1)

    log_info("Finished copying files.")
