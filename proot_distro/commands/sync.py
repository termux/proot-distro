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

# Architecture: Synchronizes a source path to a destination path, comparing
# by file size and modification time (or CRC32 checksum with --checksum) —
# a change detector, not an integrity check. Always recursive — both files
# and directories are accepted as source. Symlinks within the tree are
# copied as-is, while one named as the source itself is followed
# (`sync /sdcard box:/x`, as `copy` does); hard links become independent
# file copies; special files (block/char/FIFO/socket) are skipped within a
# tree, with a warning, and refused as the whole source. Ownership is never
# changed. Modes and timestamps are preserved, for directories as well as
# files, and a mode or timestamp that changed on its own is applied without
# rewriting the content — unless the destination carries a second link, the
# one case where a metadata change would land on an inode this command did
# not create (see _refresh_file_metadata).
# A source stored sparsely is written back sparsely. When the destination
# lacks write permission the command attempts to chmod it; failing that it
# exits with an error. With --delete, destination entries that have no
# counterpart in the source are removed after the sync pass; a source
# sitting *inside* the destination is refused rather than pruned as one of
# them, and nothing is pruned at all if the source could not be listed.
# Paths may be plain host paths or container-prefixed ('ubuntu:/etc')
# references.
#
# An entry that cannot be read or written is reported and stepped over, so
# one bad file does not cost the rest of the tree; the command exits
# non-zero when any were, the way rsync does. That holds for *every* kind
# of entry: a directory that cannot be created, a symlink the destination
# filesystem will not hold (vfat, which is what /sdcard is) and an orphan
# `--delete` cannot remove used to end the command where they stood,
# leaving the rest of the tree untransferred behind one line of output.
#
# Both roots are pinned (paths.pin_path) and every level below them is
# reached with openat(2) through proot_distro.dirfd, so nothing here ever
# resolves a path a container process could have re-pointed in the
# meantime. Two consequences worth remembering when editing:
#
#   - A destination entry that is not a directory where the source has one
#     is unlinked and replaced, never descended into. A symlink there may
#     lead outside the container, and the whole subtree would follow it.
#   - The permission fix-ups go through dirfd.make_writable, which names a
#     descriptor. chmod() has no symlink-relative form on Linux, so naming
#     an entry would apply the mode to whatever a planted link points at.
#
# The work is three passes over the tree: _collect_rels counts entries
# and records the source's relative paths, _mirror_at writes, and (with
# --delete) _collect_extras_at / _remove_extras_at prune. All three carry
# directory fds down the recursion and close them as it unwinds, so the
# number of open fds is the depth of the tree rather than its size.

import os
import stat
import sys
import zlib
from contextlib import ExitStack

from proot_distro import dirfd
from proot_distro.message import (
    log_info, log_error, crit_error, quote_path,
)
from proot_distro.paths import (
    container_locks_for_spec_pair, pin_path, refuse_src_dest_overlap,
    resolve_container_child, resolve_container_path,
)
from proot_distro.progress import clear_bar, draw_count_bar

_TMP_SUFFIX = ".~pd_sync"


class _Ctx:
    """State threaded through the recursive passes."""

    def __init__(self, src_root, dest_spec, verbose, use_checksum):
        self.src_root = src_root
        self.dest_spec = dest_spec
        self.verbose = verbose
        self.use_checksum = use_checksum
        self.total = 1
        self.done = 0
        # Every relative path seen in the source. The counting pass fills
        # it and the mirror pass adds to it, because the two look at the
        # tree at different moments: an entry created in between is
        # mirrored by the second, and --delete then removed the file it
        # had just written, having only the first pass's word for what the
        # source contains.
        self.src_rels = set()
        # Relative paths the mirror pass did not write. Two things follow
        # from an entry being in here: --delete leaves the matching
        # destination alone (it has no counterpart to compare against, and
        # pruning it would delete data on the strength of a transfer that
        # never happened), and the command reports the transfer incomplete.
        self.skipped_rels = set()
        # Entries that could not be read or written. Counted rather than
        # fatal — one entry must not abandon the rest of the tree — but
        # the command still exits non-zero, as rsync does for the same.
        self.failures = 0
        # Set when the source root itself could not be listed, which
        # leaves src_rels empty and every destination entry looking like
        # an orphan. --delete must not run on that: it would empty the
        # destination on the strength of an enumeration that failed.
        self.root_unreadable = False

    def saw(self, rel):
        """Record a source entry, keeping the progress total in step.

        The total is recomputed rather than incremented because the two
        passes overlap: pass 1 fills src_rels and pass 2 adds whatever
        appeared since, and a count that only ever grew ran past its own
        total, printing "(5/1)" and a bar wider than its twenty cells.
        """
        self.src_rels.add(rel)
        self.total = max(len(self.src_rels), 1)

    def note_failure(self, rel):
        """Record an entry that was skipped, and why it matters later.

        Idempotent: both passes touch the same tree, so a persistently
        unreadable entry is met twice, and counting it twice made one bad
        file report as "2 entries could not be transferred".
        """
        if rel not in self.skipped_rels:
            self.skipped_rels.add(rel)
            self.failures += 1

    # Both of these are for messages only, and *rel* comes from the tree
    # being walked, so both quote it: a name inside a container rootfs is
    # the guest's to choose and may carry ESC (see message.quote_path).
    def shown(self, rel):
        """The destination path as the user typed it, for messages."""
        return quote_path(
            os.path.join(self.dest_spec, rel) if rel else self.dest_spec)

    def src_shown(self, rel):
        """The source path, for messages about the reading side."""
        return quote_path(
            os.path.join(self.src_root, rel) if rel else self.src_root)

    def progress(self):
        # Suppress the bar in verbose mode: per-file log lines already
        # provide feedback and the bar would flicker between each line.
        if not self.verbose:
            draw_count_bar(self.done, self.total, unit="files")


def _rel(rel, name):
    return f"{rel}/{name}" if rel else name


def _is_special(mode):
    return (stat.S_ISBLK(mode) or stat.S_ISCHR(mode)
            or stat.S_ISFIFO(mode) or stat.S_ISSOCK(mode))


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------

def _checksum_at(dir_fd, name):
    """CRC32 of the file *name* under dir_fd."""
    fd, _ = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    try:
        crc = 0
        with open(fd, "rb", closefd=False) as fh:
            for chunk in iter(lambda: fh.read(65536), b""):
                crc = zlib.crc32(chunk, crc)
        return crc
    finally:
        os.close(fd)


def _needs_update(src_fd, src_name, src_st, dst_fd, dst_name, use_checksum):
    """Return True when dst must be (re)written from src.

    Comparison logic for regular files only:
    - Always update on size mismatch.
    - With --checksum: compare CRC32 digests.
    - Without --checksum: compare integer modification times.
    """
    try:
        dst_st = dirfd.lstat_at(dst_fd, dst_name)
    except OSError:
        return True
    if stat.S_IFMT(src_st.st_mode) != stat.S_IFMT(dst_st.st_mode):
        return True
    if src_st.st_size != dst_st.st_size:
        return True
    if use_checksum:
        try:
            return (_checksum_at(src_fd, src_name)
                    != _checksum_at(dst_fd, dst_name))
        except OSError:
            return True
    return int(src_st.st_mtime) != int(dst_st.st_mtime)


# ---------------------------------------------------------------------------
# Writing single entries
# ---------------------------------------------------------------------------

def _unlink_robust(dst_fd, name, is_dir=False):
    """Remove *name* under dst_fd, retrying with a chmod on EPERM.

    Raises OSError when the entry survives. Every caller turns that into a
    per-entry failure; this used to exit the process on the spot, which is
    the one thing a transfer must not do for a single entry.
    """
    try:
        dirfd.rmtree_at(dst_fd, name) if is_dir else os.unlink(
            name, dir_fd=dst_fd)
        return
    except PermissionError:
        pass
    dirfd.make_writable(dst_fd)
    dirfd.rmtree_at(dst_fd, name, force=True) if is_dir else os.unlink(
        name, dir_fd=dst_fd)


def _sync_dir(dst_fd, name):
    """Ensure *name* exists under dst_fd as a directory.

    Returns True when the directory was newly created; raises OSError when
    it could not be, which the caller reports and steps over.
    """
    try:
        dst_st = dirfd.lstat_at(dst_fd, name)
    except OSError:
        dst_st = None

    if dst_st is not None and not stat.S_ISDIR(dst_st.st_mode):
        # The source has a real directory here but the destination holds
        # something else. Replace it (what rsync does) rather than failing
        # or, in the symlink case, descending through it: inside a container
        # rootfs such a link may point at the host filesystem, and every
        # file of this subtree would then be written outside the container.
        # A plain file used to abort the whole sync here on mkdir's EEXIST.
        _unlink_robust(dst_fd, name)
        dst_st = None

    if dst_st is not None:
        return False

    # Created writable, with the source's metadata applied by
    # _apply_dir_metadata() once the contents are in. mkdir's mode is
    # umask-masked and so cannot preserve the source mode anyway —
    # syncing twice used to give two different results, the second run's
    # chmod correcting the first run's masked mkdir.
    try:
        os.mkdir(name, 0o700, dir_fd=dst_fd)
    except PermissionError:
        dirfd.make_writable(dst_fd)
        os.mkdir(name, 0o700, dir_fd=dst_fd)
    return True


def _apply_dir_metadata(sub_src, sub_dst):
    """Give the destination directory its source's mode and times.

    Against the descended fds, not the names: os.chmod() has no
    symlink-relative form on Linux, so naming the entry would hand a
    swapped-in link's target the mode change. Applying it only on the way
    back up also keeps a read-only source directory (0555 and friends)
    writable while its own contents are still being written, and is the
    only moment the mtime can be set — writing the contents bumps it.

    The timestamps used to be left out here, so a directory's mtime came
    out as the moment of the sync rather than the source's, in a command
    whose contract is that modes and timestamps are preserved.
    """
    dirfd.copy_metadata(sub_src, sub_dst)


def _sync_symlink(src_fd, src_name, dst_fd, dst_name, src_st=None):
    """Copy a symlink as-is. Returns True when dst changed.

    Raises OSError when the link could not be read or written, which the
    caller reports and steps over. Ending the command here instead was the
    difference between skipping one entry and abandoning the transfer: a
    destination filesystem with no symlinks at all (vfat, which is what
    /sdcard is) failed on the first one and left everything after it
    untransferred, with a single line of output to say so.
    """
    target = os.readlink(src_name, dir_fd=src_fd)

    try:
        dst_st = dirfd.lstat_at(dst_fd, dst_name)
    except OSError:
        dst_st = None

    if dst_st is not None:
        if (stat.S_ISLNK(dst_st.st_mode)
                and os.readlink(dst_name, dir_fd=dst_fd) == target):
            return False
        _unlink_robust(dst_fd, dst_name, stat.S_ISDIR(dst_st.st_mode))

    try:
        os.symlink(target, dst_name, dir_fd=dst_fd)
    except PermissionError:
        dirfd.make_writable(dst_fd)
        os.symlink(target, dst_name, dir_fd=dst_fd)

    if src_st is not None:
        dirfd.set_times_at(dst_fd, dst_name, src_st)
    return True


# What _refresh_file_metadata found: nothing to do, it was fixed in place,
# or the entry has to be rewritten after all (see below).
_META_OK, _META_FIXED, _META_REWRITE = range(3)


def _refresh_file_metadata(src_st, dst_fd, dst_name):
    """Bring an up-to-date file's mode and times into line with the source's.

    _needs_update compares type, size and mtime (or, with --checksum,
    content) — never permissions — so a `chmod +x` with no other change was
    invisible and the destination kept the old mode for good. Directories
    are given their metadata on every pass (_apply_dir_metadata), which made
    the gap a quiet one: only regular files drifted. --checksum left the
    times behind in the same way, since a file whose content matches is not
    rewritten and nothing else was setting them.

    Mode and times are applied to a descriptor, so the entry cannot be
    swapped for something else between the test and the change, and
    open_regular_at refuses anything but a regular file.

    A destination carrying more than one link is not touched at all;
    _META_REWRITE asks the caller for a full rewrite instead. This is the
    one place a transfer would otherwise write to an inode it did not
    create, and a hardlink is precisely what that rule exists for: nothing
    distinguishes one a guest made to a *host* file from an ordinary rootfs
    entry (same uid, same filesystem, no race needed), so an fchmod here
    handed a container the mode of any file the host had put within its
    reach. The rewrite goes through _sync_file, whose temp-and-rename
    leaves the other name pointing at the old inode, untouched.
    """
    try:
        fd, dst_st = dirfd.open_regular_at(dst_fd, dst_name, os.O_RDONLY)
    except OSError:
        return _META_OK
    try:
        want = stat.S_IMODE(src_st.st_mode)
        # Whole seconds, the granularity _needs_update itself compares at:
        # a filesystem that stores less precision than the source would
        # otherwise be found wanting on every single run.
        stale = int(dst_st.st_mtime) != int(src_st.st_mtime)
        if stat.S_IMODE(dst_st.st_mode) == want and not stale:
            return _META_OK
        if dst_st.st_nlink != 1:
            return _META_REWRITE
        if stat.S_IMODE(dst_st.st_mode) != want:
            os.fchmod(fd, want)
        if stale:
            os.utime(fd, ns=(src_st.st_atime_ns, src_st.st_mtime_ns))
        return _META_FIXED
    except OSError:
        return _META_OK
    finally:
        os.close(fd)


def _sync_file(src_fd, src_name, src_st, dst_fd, dst_name, ctx, rel):
    """Copy a regular file, preserving mode and mtime.

    Returns True when the destination now matches the source, False when
    the entry was left as it was — which is what tells --delete to keep
    its hands off the destination.

    Writes a sibling temp file and renames it into place, so a partial
    write never leaves the destination corrupt and an existing symlink at
    the destination name is replaced rather than written through. The temp
    file is created O_EXCL (dirfd.open_new_at), so a name already standing
    there is removed rather than written into — it may be a hardlink to a
    file outside the container, which nothing about the entry would show.

    Every failure here is per-entry: reported, counted, and stepped over.
    A failed write used to end the whole command, so one unwritable file
    left every later entry untransferred — and a container could arrange
    one at will, since a *directory* standing under the temp name is not
    a leftover to be unlinked but an EISDIR.
    """
    where = ctx.shown(rel)
    tmp = dirfd.temp_name(dst_name, _TMP_SUFFIX)
    mode = stat.S_IMODE(src_st.st_mode)

    # A directory standing where the source has a regular file. rsync refuses
    # this too (it takes --force to clear one out of the way), and renaming
    # the temp file over it could not work regardless. Reported and skipped,
    # and named for what it is instead of surfacing as EISDIR on a temp file
    # the user never asked about. _sync_dir does remove a *non*-directory in
    # the other direction — a symlink there can lead out of the container,
    # and a directory cannot.
    try:
        dst_st = dirfd.lstat_at(dst_fd, dst_name)
    except OSError:
        dst_st = None
    if dst_st is not None and stat.S_ISDIR(dst_st.st_mode):
        log_error(f"Warning: cannot replace directory '{where}' with a "
                  f"file, skipping.")
        ctx.note_failure(rel)
        return False

    try:
        sfd, sfd_st = dirfd.open_regular_at(src_fd, src_name, os.O_RDONLY)
    except OSError as exc:
        log_error(f"Warning: cannot read '{ctx.src_shown(rel)}': "
                  f"{quote_path(str(exc))}")
        ctx.note_failure(rel)
        return False

    try:
        try:
            try:
                tfd, _ = dirfd.open_new_at(dst_fd, tmp, mode)
            except PermissionError:
                dirfd.make_writable(dst_fd)
                tfd, _ = dirfd.open_new_at(dst_fd, tmp, mode)
            try:
                dirfd.copy_data(sfd, tfd, sfd_st)
                dirfd.copy_metadata(sfd, tfd, src_st)
            finally:
                os.close(tfd)
            os.replace(tmp, dst_name, src_dir_fd=dst_fd, dst_dir_fd=dst_fd)
        except OSError as exc:
            dirfd.unlink_quietly(dst_fd, tmp)
            log_error(f"Warning: cannot write to '{where}': "
                      f"{quote_path(str(exc))}")
            ctx.note_failure(rel)
            return False
        except BaseException:
            # Ctrl-C above all: the temp file has to go whatever ends the
            # write, or an interrupted sync leaves a half-copy sitting
            # next to the real one. `copy` has caught BaseException here
            # since it grew a temp file; this only ever caught OSError.
            dirfd.unlink_quietly(dst_fd, tmp)
            raise
    finally:
        os.close(sfd)
    return True


# ---------------------------------------------------------------------------
# Pass 1 — count entries and record the source's relative paths
# ---------------------------------------------------------------------------

def _record_level(src_fd, rel, ctx):
    """Add one level's entries to ctx.src_rels; return its subdirectories."""
    try:
        names = dirfd.listdir_at(src_fd)
    except OSError:
        if rel:
            ctx.note_failure(rel)
        else:
            # The root. skipped_rels cannot express "all of it" -- every
            # relative path is below this one -- and src_rels stays empty,
            # which makes every destination entry look like an orphan. Say
            # so, and let _sync_directory decline to prune.
            ctx.root_unreadable = True
            ctx.failures += 1       # no rel to key note_failure on
        log_error(f"Warning: directory '{ctx.src_shown(rel)}' is not "
                  f"readable, skipping.")
        return []

    subdirs = []
    for name in names:
        child = _rel(rel, name)
        ctx.saw(child)
        try:
            st = dirfd.lstat_at(src_fd, name)
        except OSError as exc:
            # Its type is unknown, so whether the destination holds a
            # subtree that belongs to it is unknown too.
            log_error(f"Warning: cannot stat '{ctx.src_shown(child)}': "
                      f"{quote_path(str(exc))}")
            ctx.note_failure(child)
            continue
        if stat.S_ISDIR(st.st_mode):
            subdirs.append(name)
    return subdirs


def _collect_rels(src_fd, rel, ctx):
    """Record every entry under src_fd into ctx.src_rels.

    Directories that cannot be opened are warned about once, here, and
    added to ctx.skipped_rels so neither the mirror nor --delete touches
    the matching destination subtree.

    Walked with an explicit stack, as all three passes are: how deep the
    tree goes is not this command's decision, and recursing turned one
    deeper than the interpreter's limit into a traceback (see
    dirfd.copy_tree_at, where the same change is spelled out).
    """
    # Frame layout: [fd, None, rel, pending subdirectory names, owned] —
    # the shape dirfd.close_frames expects.
    stack = [[src_fd, None, rel, None, False]]
    try:
        while stack:
            frame = stack[-1]
            fd, _, cur, pending, owned = frame
            if pending is None:
                pending = frame[3] = _record_level(fd, cur, ctx)
                pending.reverse()       # pop() from the end, in name order
            if not pending:
                stack.pop()
                if owned:
                    os.close(fd)
                continue

            name = pending.pop()
            child = _rel(cur, name)
            try:
                sub = dirfd.opendir_at(fd, name)
            except OSError:
                ctx.note_failure(child)
                log_error(f"Warning: directory '{ctx.src_shown(child)}' is "
                          f"not readable, skipping.")
                continue
            stack.append([sub, None, child, None, True])
    except BaseException:
        dirfd.close_frames(stack)
        raise


# ---------------------------------------------------------------------------
# Pass 2 — mirror the source onto the destination
# ---------------------------------------------------------------------------

def _mirror_entries(src_fd, dst_fd, rel, ctx):
    """Write one level's non-directory entries; return its subdirectories.

    An entry this cannot write is added to ctx.skipped_rels, which keeps
    --delete off the destination that stands in its place. Without that,
    a source entry the mirror stepped over still counted as "present in
    the source", so the prune pass walked into whatever the destination
    held under that name and emptied it: a source file that could not
    replace a destination *directory* took the directory's whole contents
    with it, and so did a source FIFO, which is never mirrored at all.

    Every name seen here also joins ctx.src_rels. The counting pass ran
    earlier and the source may have moved on since -- an entry created in
    between is written by this pass, and --delete, going by the first
    pass alone, removed the very file it had just transferred.
    """
    try:
        names = dirfd.listdir_at(src_fd)
    except OSError as exc:
        # Usually pass 1 met this too and has already said so, in which
        # case note_failure is a no-op. When it did not -- the level was
        # readable when it was counted and is not now -- the subtree is
        # silently not mirrored, and saying nothing meant a stale
        # destination and an exit status of 0.
        if rel not in ctx.skipped_rels:
            log_error(f"Warning: cannot read directory "
                      f"'{ctx.src_shown(rel)}': {quote_path(str(exc))}")
        ctx.note_failure(rel)
        return []

    subdirs = []
    for name in names:
        child = _rel(rel, name)
        ctx.saw(child)
        try:
            src_st = dirfd.lstat_at(src_fd, name)
        except OSError as exc:
            log_error(f"Warning: cannot stat "
                      f"'{ctx.src_shown(child)}': {quote_path(str(exc))}")
            ctx.note_failure(child)
            ctx.done += 1
            ctx.progress()
            continue

        mode = src_st.st_mode

        if _is_special(mode):
            # Never mirrored, so the destination under this name is not
            # this transfer's to judge. Said out loud, as `copy` says it:
            # a device, FIFO or socket that quietly failed to arrive is
            # not something a user should have to diff a tree to discover.
            log_error(f"Warning: skipping special file "
                      f"'{ctx.src_shown(child)}'.")
            ctx.skipped_rels.add(child)
        elif stat.S_ISDIR(mode):
            try:
                created = _sync_dir(dst_fd, name)
            except OSError as exc:
                log_error(f"Warning: cannot create directory "
                          f"'{ctx.shown(child)}': {quote_path(str(exc))}")
                # Not appended to subdirs, so the subtree is left alone —
                # and note_failure keeps --delete off the destination that
                # stands in its place.
                ctx.note_failure(child)
            else:
                subdirs.append(name)
                if ctx.verbose and created:
                    log_info(f"({ctx.done + 1}/{ctx.total}) New directory: "
                             f"{ctx.shown(child)}")
        elif stat.S_ISLNK(mode):
            existed = dirfd.exists_at(dst_fd, name)
            op = "Modified" if existed else "New"
            try:
                changed = _sync_symlink(src_fd, name, dst_fd, name, src_st)
            except OSError as exc:
                # Either readlink(2) on something that is no longer a
                # symlink — the entry was one when it was listed, so a
                # guest is changing the source underneath us — or a
                # destination that will not hold a link at all. Skipped
                # with a warning, the way every other per-entry failure in
                # this loop is.
                log_error(f"Warning: cannot copy symlink "
                          f"'{ctx.src_shown(child)}': {quote_path(str(exc))}")
                ctx.note_failure(child)
            else:
                if changed and ctx.verbose:
                    log_info(f"({ctx.done + 1}/{ctx.total}) {op} symlink: "
                             f"{ctx.shown(child)}")
        elif stat.S_ISREG(mode):
            outcome = (_META_REWRITE
                       if _needs_update(src_fd, name, src_st, dst_fd, name,
                                        ctx.use_checksum)
                       else _refresh_file_metadata(src_st, dst_fd, name))
            if outcome == _META_REWRITE:
                op = "Modified" if dirfd.exists_at(dst_fd, name) else "New"
                if _sync_file(src_fd, name, src_st, dst_fd, name, ctx,
                              child) and ctx.verbose:
                    log_info(f"({ctx.done + 1}/{ctx.total}) {op} file: "
                             f"{ctx.shown(child)}")
            elif outcome == _META_FIXED and ctx.verbose:
                log_info(f"({ctx.done + 1}/{ctx.total}) Metadata: "
                         f"{ctx.shown(child)}")

        ctx.done += 1
        ctx.progress()

    return subdirs


def _mirror_at(src_fd, dst_fd, rel, ctx):
    """Mirror the directory open at src_fd into the one at dst_fd."""
    # Frame layout: [src_fd, dst_fd, rel, pending subdirectories, owned] —
    # the shape dirfd.close_frames expects. Explicit rather than recursive
    # for the reason given in dirfd.copy_tree_at.
    stack = [[src_fd, dst_fd, rel, None, False]]
    try:
        while stack:
            frame = stack[-1]
            sfd, dfd, cur, pending, owned = frame
            if pending is None:
                pending = frame[3] = _mirror_entries(sfd, dfd, cur, ctx)
                pending.reverse()       # pop() from the end, in name order
            if not pending:
                stack.pop()
                if owned:
                    try:
                        # Only on the way back up: writing the contents
                        # bumps the mtime, and a source directory that is
                        # not writable itself must stay writable until
                        # they are all in.
                        _apply_dir_metadata(sfd, dfd)
                    finally:
                        os.close(dfd)
                        os.close(sfd)
                continue

            name = pending.pop()
            child = _rel(cur, name)
            try:
                sub_src = dirfd.opendir_at(sfd, name)
            except OSError as exc:
                # A refusal here means the entry turned into a symlink since
                # it was listed; anything else was already reported by
                # _collect_rels.
                if dirfd.is_refusal(exc):
                    # Not something _collect_rels can have reported: the
                    # entry was a directory when it listed the level.
                    log_error(f"Warning: source '{ctx.src_shown(child)}' "
                              f"changed to a symlink during the transfer, "
                              f"skipping.")
                ctx.note_failure(child)
                continue
            # Pushed before the destination is opened, so a failure there
            # leaves the source fd on the stack for close_frames.
            stack.append([sub_src, None, child, None, True])
            try:
                stack[-1][1] = dirfd.opendir_at(dfd, name)
            except OSError as exc:
                stack.pop()
                os.close(sub_src)
                if dirfd.is_refusal(exc):
                    log_error(f"Warning: '{ctx.shown(child)}' changed to a "
                              f"symlink during the transfer, skipping.")
                else:
                    log_error(f"Warning: cannot descend into "
                              f"'{ctx.shown(child)}': "
                              f"{quote_path(str(exc))}")
                ctx.note_failure(child)
    except BaseException:
        dirfd.close_frames(stack)
        raise


# ---------------------------------------------------------------------------
# Pass 3 — --delete
# ---------------------------------------------------------------------------

def _listing_at(dst_fd):
    """One level's entry names, reversed so pop() takes them in order.

    A level that cannot be read yields nothing: both prune passes step
    over what they cannot see rather than guessing at it.
    """
    try:
        names = dirfd.listdir_at(dst_fd)
    except OSError:
        return []
    names.reverse()
    return names


def _collect_extras_at(dst_fd, rel, ctx, extras):
    """Collect destination entries that have no counterpart in the source.

    Extra directories are captured whole and not descended into; a
    symlink is captured as a plain entry so it is unlinked rather than
    walked. Subtrees the mirror pass could not write are left alone
    (ctx.skipped_rels).

    The st_mode comes from lstat, so S_ISDIR is already false for a
    symlink; nothing here can be talked into walking one.
    """
    # Frame layout: [fd, None, rel, pending names, owned].
    stack = [[dst_fd, None, rel, None, False]]
    try:
        while stack:
            frame = stack[-1]
            fd, _, cur, pending, owned = frame
            if pending is None:
                pending = frame[3] = _listing_at(fd)
            if not pending:
                stack.pop()
                if owned:
                    os.close(fd)
                continue

            name = pending.pop()
            child = _rel(cur, name)
            if child in ctx.skipped_rels:
                continue
            try:
                st = dirfd.lstat_at(fd, name)
            except OSError:
                continue
            is_dir = stat.S_ISDIR(st.st_mode)

            if child not in ctx.src_rels:
                extras.append((child, is_dir))
            elif is_dir:
                try:
                    sub = dirfd.opendir_at(fd, name)
                except OSError:
                    continue
                stack.append([sub, None, child, None, True])
    except BaseException:
        dirfd.close_frames(stack)
        raise


def _restore_dir_metadata(fd, saved_st):
    """Put back the mode and times the prune pass disturbed.

    Removing an entry bumps its directory's mtime, and clearing a
    write-protected one goes through dirfd.make_writable first, which
    leaves u+rwx behind. Both undo what _apply_dir_metadata settled during
    the mirror, and the prune runs after it — so a directory that happened
    to contain an orphan came out of `--delete` stamped with the moment of
    the sync and, if it was read-only, 0755 instead of its own mode. Only
    the root escaped it, because _sync_directory applies its metadata last
    of all. Restoring the stat taken before the level was touched puts back
    exactly what the mirror had set.
    """
    if saved_st is None:
        return
    try:
        os.fchmod(fd, stat.S_IMODE(saved_st.st_mode))
    except OSError:
        pass
    try:
        os.utime(fd, ns=(saved_st.st_atime_ns, saved_st.st_mtime_ns))
    except OSError:
        pass


def _remove_extras_at(dst_fd, rel, targets, ctx, counter):
    """Delete the entries named in *targets*, walking by fd."""
    # Frame layout: [fd, None, rel, pending names, pre-delete stat, owned].
    stack = [[dst_fd, None, rel, None, None, False]]
    try:
        while stack:
            frame = stack[-1]
            fd, _, cur, pending, saved_st, owned = frame
            if pending is None:
                pending = frame[3] = _listing_at(fd)
                # Before anything in this level goes: what its metadata
                # should still say once the level and its subtree are done.
                try:
                    saved_st = frame[4] = os.fstat(fd)
                except OSError:
                    saved_st = frame[4] = None
            if not pending:
                stack.pop()
                # After the descendants too — removing one of those bumps
                # this level's mtime just as removing an entry does.
                _restore_dir_metadata(fd, saved_st)
                if owned:
                    os.close(fd)
                continue

            name = pending.pop()
            child = _rel(cur, name)
            if child in targets:
                counter[0] += 1
                if ctx.verbose:
                    log_info(f"({counter[0]}/{counter[1]}) Delete: "
                             f"{ctx.shown(child)}")
                try:
                    _unlink_robust(fd, name, targets[child])
                except OSError as exc:
                    log_error(f"Warning: cannot delete "
                              f"'{ctx.shown(child)}': {quote_path(str(exc))}")
                    ctx.note_failure(child)
                continue
            if child in ctx.skipped_rels:
                continue
            try:
                st = dirfd.lstat_at(fd, name)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                try:
                    sub = dirfd.opendir_at(fd, name)
                except OSError:
                    continue
                stack.append([sub, None, child, None, None, True])
    except BaseException:
        dirfd.close_frames(stack)
        raise


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def command_sync(args) -> None:
    """Mirror *src* to *dest*, optionally deleting orphaned entries."""
    src = args.source
    dest = args.destination
    verbose = getattr(args, "verbose", False)
    use_checksum = getattr(args, "checksum", False)
    delete = getattr(args, "delete", False)

    with ExitStack() as stack:
        for lock in container_locks_for_spec_pair(src, dest, command="sync"):
            stack.enter_context(lock)
        _do_sync(src, dest, verbose, use_checksum, delete)


def _do_sync(src, dest, verbose, use_checksum, delete):
    # Both endpoints come back with their own final component resolved —
    # the container side by the chroot walk, the host side by realpath — so
    # `sync /sdcard box:/x` transfers the directory `/sdcard` points at and
    # a destination link is written where it leads, as rsync and cp both do.
    # Links *within* the tree are preserved either way; only the endpoints
    # are followed. That the destination is resolved here and not left to
    # pin_path is what the overlap check below depends on: a host path is not
    # walked component by component, so pin_path would have followed an
    # endpoint link without ever being able to refuse one leading back into
    # the source.
    src_path = resolve_container_path(src)
    dest_path = resolve_container_path(dest)

    try:
        src_st = os.lstat(src_path)
    except OSError:
        crit_error(f"source path '{src}' does not exist.")
        sys.exit(1)

    src_is_dir = stat.S_ISDIR(src_st.st_mode)

    # A device, FIFO or socket named as the whole source. `copy` refuses
    # one here for the message; this used to return from _sync_single
    # without a word and report "Finished synchronizing", so the user was
    # told a transfer succeeded that had not happened at all.
    if not (src_is_dir or stat.S_ISREG(src_st.st_mode)
            or stat.S_ISLNK(src_st.st_mode)):
        crit_error(f"cannot sync '{src}': not a regular file or directory.")
        sys.exit(1)

    if src_is_dir and not os.access(src_path, os.R_OK | os.X_OK):
        crit_error(f"source directory '{src}' is not readable.")
        sys.exit(1)

    # Pruning is defined against the contents of a source directory. With
    # a single file there is nothing to enumerate and nothing was ever
    # removed, so the flag was accepted and quietly ignored -- rsync says
    # so out loud instead of leaving the user to discover it.
    if delete and not src_is_dir:
        crit_error("option '--delete' needs a directory as the source.")
        sys.exit(1)

    # If src is a file and dest is an existing dir, place file inside it.
    # Appended through the resolver: the name is a component inside the
    # container like any other, and may itself be a symlink.
    if not src_is_dir and os.path.isdir(dest_path):
        dest_path = resolve_container_child(dest, dest_path,
                                            os.path.basename(src_path))

    # Both ends are final now, which is the earliest point a planted symlink
    # can no longer hide that they overlap.
    refuse_src_dest_overlap(src, src_path, dest, dest_path, pruning=delete)

    log_info("Synchronizing files...")
    log_info(f"Source: '{quote_path(src_path)}'")
    log_info(f"Destination: '{quote_path(dest_path)}'")

    ctx = _Ctx(src_path, dest, verbose, use_checksum)

    # Pin both roots. inside=True for a directory sync: everything is
    # written *underneath* the root, so the root's own name must be
    # covered too — a root that became a symlink is refused, not
    # followed. The pins are held for the whole transfer. create=True
    # makes the destination root along that same walk; os.makedirs() on
    # the path would follow a symlink planted after the resolve and build
    # the tree outside the container before the pin could refuse.
    #
    # It is tied to src_is_dir because that is rsync's rule and not an
    # oversight: a directory transfer creates its destination directory,
    # a single file will not invent the parents it is addressed through
    # (rsync wants --mkpath for that). `copy` does create them, which is
    # a deliberate convenience of its own; the two commands differ here
    # because the tools they follow do.
    with ExitStack() as pins:
        src_pin = pins.enter_context(pin_path(src, src_path,
                                              inside=src_is_dir))
        dest_pin = pins.enter_context(pin_path(dest, dest_path,
                                               inside=src_is_dir,
                                               create=src_is_dir))
        try:
            if src_is_dir:
                _sync_directory(src_pin, dest_pin, ctx, delete)
            else:
                _sync_single(src_pin, dest_pin, src_st, ctx)
        except KeyboardInterrupt:
            clear_bar()
            log_error("Aborted by user.")
            sys.exit(1)
        except OSError as exc:
            # The net `copy` has always had. Every call the passes make is
            # guarded where a warn-and-skip is the right answer; what reaches
            # here is a race nothing can carry on through, and it used to
            # leave a traceback in place of a message. The strings carry the
            # name the call failed on, straight from the tree being walked.
            clear_bar()
            log_error(f"Error: {quote_path(str(exc))}")
            sys.exit(1)

    clear_bar()
    if ctx.failures:
        # Each of these was reported where it happened and stepped over, so
        # that one bad entry did not abandon the rest of the tree. The exit
        # status still has to say the transfer was incomplete: it used to
        # come back 0 after skipping an unreadable source directory, which
        # is the one thing a backup script cannot afford to be told.
        plural = "entry" if ctx.failures == 1 else "entries"
        log_error(f"Error: {ctx.failures} {plural} could not be "
                  f"transferred.")
        sys.exit(1)
    log_info("Finished synchronizing.")


def _sync_single(src_pin, dest_pin, src_st, ctx):
    """Sync a source that is a single file or symlink.

    The type was settled in _do_sync; the tests below are what is left if
    it changed since, in which case there is nothing to transfer.
    """
    mode = src_st.st_mode
    shown = ctx.shown("")
    if _is_special(mode) or not (stat.S_ISLNK(mode) or stat.S_ISREG(mode)):
        log_error(f"Warning: source '{ctx.src_shown('')}' is no longer a "
                  f"regular file or directory, skipping.")
        ctx.note_failure("")
        return
    if stat.S_ISLNK(mode):
        try:
            _sync_symlink(src_pin.dir_fd, src_pin.leaf,
                          dest_pin.dir_fd, dest_pin.leaf, src_st)
        except OSError as exc:
            # Per-entry here as well, even though the entry is the whole
            # transfer: it keeps the message and the exit status the same
            # shape as the tree case rather than a bare "Error: <errno>".
            log_error(f"Warning: cannot copy symlink '{shown}': "
                      f"{quote_path(str(exc))}")
            ctx.note_failure("")
        return
    outcome = (_META_REWRITE
               if _needs_update(src_pin.dir_fd, src_pin.leaf, src_st,
                                dest_pin.dir_fd, dest_pin.leaf,
                                ctx.use_checksum)
               else _refresh_file_metadata(src_st, dest_pin.dir_fd,
                                           dest_pin.leaf))
    if outcome == _META_REWRITE:
        _sync_file(src_pin.dir_fd, src_pin.leaf, src_st,
                   dest_pin.dir_fd, dest_pin.leaf, ctx, "")


def _sync_directory(src_pin, dest_pin, ctx, delete):
    """Sync a directory source: count, mirror, then optionally prune."""
    src_fd = dirfd.reopen(src_pin.dir_fd, src_pin.leaf)
    try:
        dst_fd = dirfd.reopen(dest_pin.dir_fd, dest_pin.leaf)
        try:
            _collect_rels(src_fd, "", ctx)   # ctx.saw() sets ctx.total
            _mirror_at(src_fd, dst_fd, "", ctx)
            clear_bar()

            if delete:
                _prune(dst_fd, ctx)
            # Last of all, and only now: the mode may take the write bit
            # off the directory everything above still had to write into,
            # and both the mirror and the prune bump its mtime.
            dirfd.copy_metadata(src_fd, dst_fd)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)


def _prune(dst_fd, ctx):
    """Remove destination entries the source has no counterpart for.

    Declined outright when the source root could not be listed. src_rels
    is then empty through no fault of the destination's, so every entry
    in it looks like an orphan and the pass emptied the lot -- rsync
    disables --delete on an I/O error for the same reason, and wants
    --ignore-errors before it will go ahead anyway.
    """
    if ctx.root_unreadable:
        log_error("Warning: not deleting anything: the source could not "
                  "be listed, so nothing can be called an orphan of it.")
        return
    extras = []
    _collect_extras_at(dst_fd, "", ctx, extras)
    targets = dict(extras)
    counter = [0, len(extras)]
    _remove_extras_at(dst_fd, "", targets, ctx, counter)
