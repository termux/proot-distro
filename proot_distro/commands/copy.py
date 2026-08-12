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
# trees preserving symlinks (like cp -a); a destination that already exists
# as a directory receives the source inside it, as cp and mv both do.
#
# A recursive copy merges into a destination tree that is already there,
# as cp -a does, so running the same copy twice updates it instead of
# stopping on the first mkdir's EEXIST. --move keeps rename(2)'s rule
# instead and refuses a populated destination directory.
#
# Hard links become independent copies (nothing distinguishes one a guest
# made to a host file from an ordinary entry — see dirfd.open_new_at), and
# a sparsely stored file is written back sparsely. An entry that cannot be
# read is reported and stepped over rather than ending the transfer, which
# is `cp -r`'s behaviour; the command exits non-zero when any were, and
# --move then leaves the source in place, since the copy it would be
# deleting is incomplete — including when the only thing missing is a
# device, FIFO or socket, which no tree this module writes carries across.

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
    container_locks_for_spec_pair,
    pin_path,
    refuse_src_dest_overlap,
    resolve_container_child,
    resolve_container_path,
)
from proot_distro.progress import (
    clear_bar, draw_count_bar, progress_active,
)


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


def _copy_tree_pinned(src_pin, dest_pin, verbose, dest_display, *,
                      merge=False):
    """Recreate the source directory under the destination, fd by fd.

    Replaces shutil.copytree(symlinks=True). copytree walks by path, so
    every directory it creates and descends into is addressed by name and
    can be swapped for a symlink mid-transfer; carrying the fds down the
    walk removes that entirely.

    Returns (failures, skipped): entries that could not be copied, and
    device/FIFO/socket entries deliberately left out. Both are reported one
    by one and stepped over rather than ending the transfer (see
    dirfd.copy_tree_at), so the caller has to ask — `--move` in particular
    must remove nothing when either count is non-zero, since the source
    holds the only copy of whatever did not make it across.

    merge=True lets the walk write into a destination tree that already
    exists (see dirfd.copy_tree_at). `--move`'s cross-device fallback
    leaves it off so that it refuses a populated destination directory,
    which is what the rename(2) it stands in for would have done.
    """
    failures = [0]
    skipped = [0]
    src_fd = _opendir_pinned(src_pin)
    try:
        src_st = os.fstat(src_fd)
        # Created writable; copy_metadata() below applies the real mode
        # once the contents are in (see dirfd.copy_tree_at). Without merge,
        # mkdirat refuses to create over anything that already exists,
        # including a planted symlink — copytree's behaviour too.
        try:
            os.mkdir(dest_pin.leaf, 0o700, dir_fd=dest_pin.dir_fd)
        except FileExistsError:
            if not merge:
                raise OSError(errno.EEXIST, os.strerror(errno.EEXIST),
                              dest_display) from None
        except OSError as exc:
            # The fd-relative call only knows the leaf; report the path.
            raise OSError(exc.errno, exc.strerror, dest_display) from None
        # O_NOFOLLOW: a name the mkdir did not create is only descended
        # into when it really is a directory. Re-raised with the path for
        # the same reason the mkdir is — merging onto a plain file reports
        # ENOTDIR from here, and the leaf alone does not say where.
        try:
            dst_fd = dirfd.opendir_at(dest_pin.dir_fd, dest_pin.leaf)
        except OSError as exc:
            raise OSError(exc.errno, exc.strerror, dest_display) from None
        try:
            dirfd.make_writable(dst_fd)
            # Entry names come from the tree being copied, so they are
            # quoted: a rootfs name may carry ESC (see message.quote_path).
            def shown(rel):
                return quote_path(os.path.join(dest_display, rel))

            # An entry that could not be copied is nearly always one that
            # could not be *read*, so it is named on the source side —
            # `cp` reports the path it failed to access, and pointing at a
            # destination that was never written reads as the wrong fault.
            def src_shown(rel):
                return quote_path(os.path.join(str(src_pin), rel)
                                  if rel else str(src_pin))

            # The count is a whole extra walk of the source, so it is
            # only paid for when a bar will actually be drawn: --verbose
            # prints a line per entry instead, and off a TTY (or under
            # --quiet) nothing is drawn at all.
            total = (0 if verbose or not progress_active()
                     else dirfd.count_tree_at(src_fd))
            done = [0]

            def on_entry(rel):
                done[0] += 1
                if verbose:
                    log_info(f"Copying: '{shown(rel)}'")
                elif total:
                    draw_count_bar(done[0], total, unit="entries")

            def on_skip(rel):
                done[0] += 1
                skipped[0] += 1
                warn(f"skipping special file '{shown(rel)}'.")

            def on_error(rel, exc):
                failures[0] += 1
                done[0] += 1
                # No clear_bar() needed: message.msg() erases the partial
                # progress line before every write.
                log_error(f"Warning: cannot copy '{src_shown(rel)}': "
                          f"{quote_path(exc.strerror or str(exc))}")

            dirfd.copy_tree_at(src_fd, dst_fd, merge=merge, on_entry=on_entry,
                               on_skip=on_skip, on_error=on_error)
            dirfd.copy_metadata(src_fd, dst_fd, src_st)
        finally:
            clear_bar()
            os.close(dst_fd)
    finally:
        os.close(src_fd)
    return failures[0], skipped[0]


def _move_pinned(src_pin, dest_pin, verbose=False):
    """Move via renameat, falling back to copy+remove across devices.

    rename(2) replaces a symlink sitting at the destination rather than
    following it, and both ends are named relative to a pinned fd, so the
    fast path needs no further protection.

    Returns the number of entries the fallback did not carry across.
    Nothing is removed from the source when that is non-zero: a move whose
    copy half skipped an entry would otherwise delete the one copy of it.
    Entries skipped *by design* count here too — a device, FIFO or socket
    is left out of every tree this module writes, which is a warning during
    a copy but silent data loss during a move, and on Termux the common
    move (a rootfs onto /sdcard) is exactly the cross-device one.
    """
    try:
        os.rename(src_pin.leaf, dest_pin.leaf,
                  src_dir_fd=src_pin.dir_fd, dst_dir_fd=dest_pin.dir_fd)
        return 0
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    # Across devices the move becomes copy + remove. The type comes from
    # the pinned fd rather than the earlier path probe, so a symlink is
    # recognised as one and recreated verbatim — never followed, and
    # never dereferenced, which is what rename(2) would have done too.
    src_st = dirfd.lstat_at(src_pin.dir_fd, src_pin.leaf)

    # rename(2) replaced whatever the destination name held — a symlink
    # included — without following it. Off the fast path that has to be done
    # by hand: unlink the name, then create fresh. Writing into a name that
    # is still there could go through a hardlink to a file outside the
    # container, and os.symlink() would just refuse with EEXIST. A directory
    # source is left to _copy_tree_pinned, whose mkdir declines to overwrite
    # anything, as rename(2) declines a non-empty or non-directory target.
    if not stat.S_ISDIR(src_st.st_mode):
        dirfd.unlink_quietly(dest_pin.dir_fd, dest_pin.leaf)

    if stat.S_ISLNK(src_st.st_mode):
        dirfd.copy_symlink_at(src_pin.dir_fd, src_pin.leaf,
                              dest_pin.dir_fd, dest_pin.leaf, src_st)
        os.unlink(src_pin.leaf, dir_fd=src_pin.dir_fd)
    elif stat.S_ISDIR(src_st.st_mode):
        failures, skipped = _copy_tree_pinned(src_pin, dest_pin, verbose,
                                              str(dest_pin))
        if failures or skipped:
            # The copy half is incomplete, so the source is now the only
            # place some of those entries exist. Keep it.
            log_error("Source left in place: the copy did not complete.")
            return failures + skipped
        dirfd.rmtree_at(src_pin.dir_fd, src_pin.leaf, force=True)
    else:
        dirfd.copy_file_at(src_pin.dir_fd, src_pin.leaf,
                           dest_pin.dir_fd, dest_pin.leaf)
        os.unlink(src_pin.leaf, dir_fd=src_pin.dir_fd)
    return 0


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

    # The lstat, not os.path.isdir(): in move mode src_path's own final
    # component is deliberately left unresolved, and isdir() would resolve
    # a *container* link against the host filesystem. Both readings agree
    # for a plain copy, where src_path holds no link by then.
    src_is_dir = stat.S_ISDIR(src_mode)
    if src_is_dir and not recursive and not move_mode:
        crit_error(f"source path is a directory. Use option '--recursive' "
                   f"to copy directories.")
        sys.exit(1)

    # Anything copied or moved onto an existing directory lands inside it,
    # which is what cp and mv both do and what the mkdir below cannot: a
    # recursive copy whose destination already existed died on EEXIST
    # instead of creating <dest>/<source name>. shutil did the appending
    # implicitly; spell it out so the destination names the entry we are
    # about to create. The name is appended through the resolver, not
    # joined on: it is a path component inside the container like any
    # other, and may be a symlink.
    #
    # mv also moves inside the directory a destination *link* points at,
    # leaving the link where it is, so the question is asked of the target
    # while dest_path keeps the name rename(2) acts on. It has to be asked
    # with container semantics: os.path.isdir() on the unresolved leaf
    # resolves the guest's link against the *host* tree, which both invented
    # directories the container never had (`/dir -> /tmp`, dangling inside,
    # a directory outside) and destroyed links whose target only the
    # container has (`current -> /opt/app/releases/v1` became a file).
    dest_target = resolve_container_path(dest) if move_mode else dest_path
    if os.path.isdir(dest_target):
        dest_path = resolve_container_child(dest, dest_target,
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
    failures = 0
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
                failures = _move_pinned(src_pin, dest_pin, verbose)
            else:
                log_info("Copying files, this may take a while...")
                if src_is_dir:
                    # merge=True: a destination tree that already exists is
                    # written into rather than refused, which is what cp -a
                    # does and what running the same copy twice needs.
                    failures, _ = _copy_tree_pinned(src_pin, dest_pin,
                                                    verbose, dest_path,
                                                    merge=True)
                else:
                    if verbose:
                        log_info(f"Copying: '{quote_path(src_path)}' -> "
                                 f"'{quote_path(dest_path)}'")
                    # replace=True: the destination is a name the user
                    # chose and may already exist, so it is written as a
                    # fresh inode and renamed over (see dirfd.copy_file_at).
                    dirfd.copy_file_at(src_pin.dir_fd, src_pin.leaf,
                                       dest_pin.dir_fd, dest_pin.leaf,
                                       replace=True)
    except KeyboardInterrupt:
        clear_bar()
        log_error("Aborted by user.")
        sys.exit(1)
    except OSError as exc:
        # The strings carry the name the call failed on, straight from the
        # tree being copied.
        clear_bar()
        log_error(f"Error: {quote_path(str(exc))}")
        sys.exit(1)

    if failures:
        # Each was reported where it happened and stepped over, so that one
        # unreadable entry did not cost the whole tree. The status still has
        # to say the copy is incomplete, which is what `cp -r` does too.
        plural = "entry" if failures == 1 else "entries"
        log_error(f"Error: {failures} {plural} could not be copied.")
        sys.exit(1)

    log_info("Finished copying files.")
