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
from proot_distro.message import (
    log_info, log_error, crit_error, quote_path, warn,
)
from proot_distro.paths import (
    container_from_spec,
    container_locks_for_spec_pair,
    pin_path,
    refuse_src_dest_overlap,
    resolve_container_child,
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
        # Created writable; copy_metadata() below applies the real mode
        # once the contents are in (see dirfd.copy_tree_at).
        try:
            os.mkdir(dest_pin.leaf, 0o700, dir_fd=dest_pin.dir_fd)
        except OSError as exc:
            # The fd-relative call only knows the leaf; report the path.
            raise OSError(exc.errno, exc.strerror, dest_display) from None
        dst_fd = dirfd.opendir_at(dest_pin.dir_fd, dest_pin.leaf)
        try:
            # Entry names come from the tree being copied, so they are
            # quoted: a rootfs name may carry ESC (see message.quote_path).
            def shown(rel):
                return quote_path(os.path.join(dest_display, rel))

            def on_entry(rel):
                if verbose:
                    log_info(f"Copying: '{shown(rel)}'")

            def on_skip(rel):
                warn(f"skipping special file '{shown(rel)}'.")

            dirfd.copy_tree_at(src_fd, dst_fd,
                               on_entry=on_entry, on_skip=on_skip)
            dirfd.copy_metadata(src_fd, dst_fd, src_st)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def _move_pinned(src_pin, dest_pin):
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

    # Across devices the move becomes copy + remove. The type comes from
    # the pinned fd rather than the earlier path probe, so a symlink is
    # recognised as one and recreated verbatim — never followed, and
    # never dereferenced, which is what rename(2) would have done too.
    src_st = dirfd.lstat_at(src_pin.dir_fd, src_pin.leaf)
    if stat.S_ISLNK(src_st.st_mode):
        dirfd.copy_symlink_at(src_pin.dir_fd, src_pin.leaf,
                              dest_pin.dir_fd, dest_pin.leaf, src_st)
        os.unlink(src_pin.leaf, dir_fd=src_pin.dir_fd)
    elif stat.S_ISDIR(src_st.st_mode):
        _copy_tree_pinned(src_pin, dest_pin, False, str(dest_pin))
        dirfd.rmtree_at(src_pin.dir_fd, src_pin.leaf, force=True)
    else:
        dirfd.copy_file_at(src_pin.dir_fd, src_pin.leaf,
                           dest_pin.dir_fd, dest_pin.leaf)
        os.unlink(src_pin.leaf, dir_fd=src_pin.dir_fd)


def _do_copy(src, dest, verbose, move_mode, recursive):
    # A move acts on the entries themselves, so neither final component is
    # dereferenced: rename(2) moves a symlink rather than what it points at,
    # and replaces one sitting at the destination rather than writing
    # through it, which is what mv does. A plain copy keeps cp's semantics
    # and follows both.
    src_path = resolve_container_path(src, deref_leaf=not move_mode)
    dest_path = resolve_container_path(dest, deref_leaf=not move_mode)

    # Reject '.' or '..' as destination component (but allow as source).
    dest_base = os.path.basename(dest_path)
    if dest_base in (".", ".."):
        crit_error("paths '.' and '..' are not allowed as copy destination.")
        sys.exit(1)

    # A host source spelled as a symlink is copied by content, the way cp
    # (and the shutil implementation this replaced) does. The container
    # side gets this from resolve_container_path, which walks every
    # component including the last; host paths are not walked at all, so
    # the dereference happens here instead. Without it the O_NOFOLLOW
    # open below refuses the source outright — `copy -r /sdcard box:/x`
    # is an ordinary thing to ask for on Termux.
    if not move_mode and container_from_spec(src) is None:
        src_path = os.path.realpath(src_path)

    # A move renames the entry, so a dangling symlink is a perfectly good
    # source — mv moves the link. os.path.exists() follows it and would
    # call the path missing.
    if not (os.path.lexists(src_path) if move_mode
            else os.path.exists(src_path)):
        crit_error(f"cannot copy '{src}' because the path does not exist.")
        sys.exit(1)

    # A device, FIFO or socket named as the source endpoint. Refused here
    # for a clear message; dirfd.open_regular_at() refuses it again on the
    # pinned fd, which is what covers one planted after this check, and
    # keeps the open from blocking on a pipe with no writer.
    try:
        src_mode = os.lstat(src_path).st_mode
    except OSError as exc:
        crit_error(f"cannot copy '{src}': {exc.strerror}.")
        sys.exit(1)
    if not (stat.S_ISREG(src_mode) or stat.S_ISDIR(src_mode)
            or stat.S_ISLNK(src_mode)):
        crit_error(f"cannot copy '{src}': not a regular file or directory.")
        sys.exit(1)

    # A symlink only reaches here in move mode, where nothing reads the
    # source: rename(2) moves the link and the EXDEV fallback recreates it
    # from readlink(2). Testing the target's permissions would reject a
    # dangling link, and an unreadable one for no reason.
    if not stat.S_ISLNK(src_mode) and not os.access(src_path, os.R_OK):
        crit_error(f"source path '{quote_path(src_path)}' is not readable.")
        sys.exit(1)

    src_is_dir = os.path.isdir(src_path)
    if src_is_dir and not recursive and not move_mode:
        crit_error(f"source path is a directory. Use option '--recursive' "
                   f"to copy directories.")
        sys.exit(1)

    # A file copied onto an existing directory lands inside it, and so
    # does a moved directory. shutil did this implicitly; spell it out so
    # the destination names the entry we are about to create. The name is
    # appended through the resolver, not joined on: it is a path
    # component inside the container like any other, and may be a symlink.
    if (not src_is_dir or move_mode) and os.path.isdir(dest_path):
        dest_path = resolve_container_child(dest, dest_path,
                                            os.path.basename(src_path),
                                            deref_leaf=not move_mode)

    # Both ends are final now, which is the earliest point a planted symlink
    # can no longer hide that they overlap.
    refuse_src_dest_overlap(src, src_path, dest, dest_path,
                            deref_leaf=not move_mode)

    log_info(f"Source: '{quote_path(src_path)}'")
    log_info(f"Destination: '{quote_path(dest_path)}'")

    dest_dir = os.path.dirname(dest_path)
    if not os.path.isdir(dest_dir):
        log_info(f"Creating directory '{quote_path(dest_dir)}'...")

    # Pin both endpoints, then address the filesystem only through the
    # pinned fds: neither the endpoints nor anything the walk creates
    # below them can be redirected by a symlink appearing mid-transfer.
    # The destination's missing parents are made by that same walk
    # (create=True) — making them by path first would write through a
    # symlink planted after the resolve, before the pin could refuse.
    try:
        with ExitStack() as pins:
            src_pin = pins.enter_context(pin_path(src, src_path))
            dest_pin = pins.enter_context(pin_path(dest, dest_path,
                                                   create=True))

            if move_mode:
                log_info("Moving files...")
                if verbose:
                    log_info(f"Moving: '{quote_path(src_path)}' -> "
                             f"'{quote_path(dest_path)}'")
                _move_pinned(src_pin, dest_pin)
            else:
                log_info("Copying files, this may take a while...")
                if src_is_dir:
                    _copy_tree_pinned(src_pin, dest_pin, verbose, dest_path)
                else:
                    if verbose:
                        log_info(f"Copying: '{quote_path(src_path)}' -> "
                                 f"'{quote_path(dest_path)}'")
                    dirfd.copy_file_at(src_pin.dir_fd, src_pin.leaf,
                                       dest_pin.dir_fd, dest_pin.leaf)
    except KeyboardInterrupt:
        clear_bar()
        log_error("Aborted by user.")
        sys.exit(1)
    except OSError as exc:
        # The strings carry the name the call failed on, straight from the
        # tree being copied.
        log_error(f"Error: {quote_path(str(exc))}")
        sys.exit(1)

    log_info("Finished copying files.")
