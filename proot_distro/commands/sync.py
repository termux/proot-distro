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
# by file size and modification time (or CRC32 checksum with --checksum).
# Always recursive — both files and directories are accepted as source.
# Symlinks are copied as-is; hard links become independent file copies;
# special files (block/char/FIFO/socket) are silently skipped. Ownership is
# never changed. Modes and timestamps are preserved. When the destination
# lacks write permission the command attempts to chmod it; failing that it
# exits with an error. With --delete, destination entries that have no
# counterpart in the source are removed after the sync pass. Paths may be
# plain host paths or container-prefixed ('ubuntu:/etc') references.
#
# Both roots are pinned (paths.pin_path) and every level below them is
# reached with openat(2) through proot_distro.dirfd, so nothing here ever
# resolves a path a container process could have re-pointed in the
# meantime. Two consequences worth remembering when editing:
#
#   - A destination entry that is a symlink where the source has a
#     directory is unlinked and replaced, never descended into. It may
#     lead outside the container, and the whole subtree would follow it.
#   - The permission fix-ups act on directory fds (fchmod) and skip
#     symlinks, because chmod() has no symlink-relative form on Linux
#     and would otherwise apply to whatever a planted link points at.
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
from proot_distro.message import log_info, log_error, crit_error
from proot_distro.paths import (
    container_locks_for_spec_pair, pin_path, resolve_container_path,
)
from proot_distro.progress import clear_bar, draw_count_bar

_TMP_SUFFIX = ".~pd_sync"


class _Ctx:
    """State threaded through the recursive passes."""

    def __init__(self, src_root, dest_spec, dest_root, verbose, use_checksum):
        self.src_root = src_root
        self.dest_spec = dest_spec
        self.dest_root = dest_root
        self.verbose = verbose
        self.use_checksum = use_checksum
        self.total = 1
        self.done = 0
        self.src_rels = set()
        self.skipped_rels = set()

    def shown(self, rel):
        """The destination path as the user typed it, for messages."""
        return os.path.join(self.dest_spec, rel) if rel else self.dest_spec

    def src_shown(self, rel):
        """The source path, for messages about the reading side."""
        return os.path.join(self.src_root, rel) if rel else self.src_root

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
    fd = dirfd.open_file_at(dir_fd, name, os.O_RDONLY)
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
    """Remove *name* under dst_fd, retrying with a chmod on EPERM."""
    try:
        dirfd.rmtree_at(dst_fd, name) if is_dir else os.unlink(
            name, dir_fd=dst_fd)
    except PermissionError:
        dirfd.make_writable(dst_fd)
        try:
            dirfd.rmtree_at(dst_fd, name, force=True) if is_dir else os.unlink(
                name, dir_fd=dst_fd)
        except OSError as exc:
            log_error(f"Cannot delete '{name}': {exc}")
            sys.exit(1)
    except OSError as exc:
        log_error(f"Cannot delete '{name}': {exc}")
        sys.exit(1)


def _sync_dir(dst_fd, name, src_st):
    """Ensure *name* exists under dst_fd as a directory.

    Returns True when the directory was newly created.
    """
    try:
        dst_st = dirfd.lstat_at(dst_fd, name)
    except OSError:
        dst_st = None

    if dst_st is not None and stat.S_ISLNK(dst_st.st_mode):
        # The source has a real directory here but the destination holds
        # a symlink. Replace it (what rsync does) rather than descending
        # through it: inside a container rootfs such a link may point at
        # the host filesystem, and every file of this subtree would then
        # be written outside the container.
        _unlink_robust(dst_fd, name)
        dst_st = None

    if dst_st is not None and stat.S_ISDIR(dst_st.st_mode):
        try:
            os.chmod(name, stat.S_IMODE(src_st.st_mode), dir_fd=dst_fd)
        except OSError:
            pass
        return False

    try:
        os.mkdir(name, stat.S_IMODE(src_st.st_mode), dir_fd=dst_fd)
    except PermissionError:
        dirfd.make_writable(dst_fd)
        try:
            os.mkdir(name, stat.S_IMODE(src_st.st_mode), dir_fd=dst_fd)
        except OSError as exc:
            log_error(f"Cannot create directory '{name}': {exc}")
            sys.exit(1)
    except OSError as exc:
        log_error(f"Cannot create directory '{name}': {exc}")
        sys.exit(1)
    return True


def _sync_symlink(src_fd, src_name, dst_fd, dst_name, src_st=None):
    """Copy a symlink as-is. Returns True when dst changed."""
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
        try:
            os.symlink(target, dst_name, dir_fd=dst_fd)
        except OSError as exc:
            log_error(f"Cannot create symlink '{dst_name}': {exc}")
            sys.exit(1)
    except OSError as exc:
        log_error(f"Cannot create symlink '{dst_name}': {exc}")
        sys.exit(1)

    if src_st is not None:
        dirfd.set_times_at(dst_fd, dst_name, src_st)
    return True


def _sync_file(src_fd, src_name, src_st, dst_fd, dst_name):
    """Copy a regular file, preserving mode and mtime.

    Writes a sibling temp file and renames it into place, so a partial
    write never leaves the destination corrupt and an existing symlink at
    the destination name is replaced rather than written through.
    """
    tmp = dst_name + _TMP_SUFFIX
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    mode = stat.S_IMODE(src_st.st_mode)

    try:
        sfd = dirfd.open_file_at(src_fd, src_name, os.O_RDONLY)
    except OSError as exc:
        log_error(f"Warning: cannot read '{src_name}': {exc}")
        return

    try:
        try:
            tfd = dirfd.open_file_at(dst_fd, tmp, flags, mode)
        except PermissionError:
            dirfd.make_writable(dst_fd)
            tfd = dirfd.open_file_at(dst_fd, tmp, flags, mode)
        try:
            dirfd.copy_data(sfd, tfd)
            dirfd.copy_metadata(sfd, tfd, src_st)
        finally:
            os.close(tfd)
        os.replace(tmp, dst_name, src_dir_fd=dst_fd, dst_dir_fd=dst_fd)
    except OSError as exc:
        try:
            os.unlink(tmp, dir_fd=dst_fd)
        except OSError:
            pass
        log_error(f"Cannot write to '{dst_name}': {exc}")
        sys.exit(1)
    finally:
        os.close(sfd)


# ---------------------------------------------------------------------------
# Pass 1 — count entries and record the source's relative paths
# ---------------------------------------------------------------------------

def _collect_rels(src_fd, rel, ctx):
    """Record every entry under src_fd into ctx.src_rels.

    Directories that cannot be opened are warned about once, here, and
    added to ctx.skipped_rels so neither the mirror nor --delete touches
    the matching destination subtree.
    """
    try:
        names = dirfd.listdir_at(src_fd)
    except OSError as exc:
        if rel:
            ctx.skipped_rels.add(rel)
        log_error(f"Warning: directory '{ctx.src_shown(rel)}' is not "
                  f"readable, skipping.")
        return

    subdirs = []
    for name in names:
        child = _rel(rel, name)
        ctx.src_rels.add(child)
        try:
            st = dirfd.lstat_at(src_fd, name)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode):
            subdirs.append(name)

    for name in subdirs:
        try:
            sub = dirfd.opendir_at(src_fd, name)
        except OSError:
            child = _rel(rel, name)
            ctx.skipped_rels.add(child)
            log_error(f"Warning: directory '{ctx.src_shown(child)}' is not "
                      f"readable, skipping.")
            continue
        try:
            _collect_rels(sub, _rel(rel, name), ctx)
        finally:
            os.close(sub)


# ---------------------------------------------------------------------------
# Pass 2 — mirror the source onto the destination
# ---------------------------------------------------------------------------

def _mirror_at(src_fd, dst_fd, rel, ctx):
    """Mirror the directory open at src_fd into the one at dst_fd."""
    try:
        names = dirfd.listdir_at(src_fd)
    except OSError:
        return  # already reported by _collect_rels

    subdirs = []
    for name in names:
        child = _rel(rel, name)
        try:
            src_st = dirfd.lstat_at(src_fd, name)
        except OSError as exc:
            log_error(f"Warning: cannot stat "
                      f"'{ctx.src_shown(child)}': {exc}")
            ctx.done += 1
            ctx.progress()
            continue

        mode = src_st.st_mode

        if _is_special(mode):
            pass
        elif stat.S_ISDIR(mode):
            created = _sync_dir(dst_fd, name, src_st)
            subdirs.append(name)
            if ctx.verbose and created:
                log_info(f"({ctx.done + 1}/{ctx.total}) New directory: "
                         f"{ctx.shown(child)}")
        elif stat.S_ISLNK(mode):
            existed = dirfd.exists_at(dst_fd, name)
            op = "Modified" if existed else "New"
            if _sync_symlink(src_fd, name, dst_fd, name, src_st) and ctx.verbose:
                log_info(f"({ctx.done + 1}/{ctx.total}) {op} symlink: "
                         f"{ctx.shown(child)}")
        elif stat.S_ISREG(mode):
            if _needs_update(src_fd, name, src_st, dst_fd, name,
                             ctx.use_checksum):
                op = "Modified" if dirfd.exists_at(dst_fd, name) else "New"
                _sync_file(src_fd, name, src_st, dst_fd, name)
                if ctx.verbose:
                    log_info(f"({ctx.done + 1}/{ctx.total}) {op} file: "
                             f"{ctx.shown(child)}")

        ctx.done += 1
        ctx.progress()

    for name in subdirs:
        child = _rel(rel, name)
        try:
            sub_src = dirfd.opendir_at(src_fd, name)
        except OSError as exc:
            # A refusal here means the entry turned into a symlink since
            # it was listed; anything else was already reported by
            # _collect_rels.
            if dirfd.is_refusal(exc):
                log_error(f"Warning: source '{ctx.src_shown(child)}' "
                          f"changed to a symlink during the transfer, "
                          f"skipping.")
            continue
        try:
            try:
                sub_dst = dirfd.opendir_at(dst_fd, name)
            except OSError as exc:
                if dirfd.is_refusal(exc):
                    log_error(f"Warning: '{ctx.shown(child)}' changed to a "
                              f"symlink during the transfer, skipping.")
                else:
                    log_error(f"Warning: cannot descend into "
                              f"'{ctx.shown(child)}': {exc}")
                continue
            try:
                _mirror_at(sub_src, sub_dst, child, ctx)
            finally:
                os.close(sub_dst)
        finally:
            os.close(sub_src)


# ---------------------------------------------------------------------------
# Pass 3 — --delete
# ---------------------------------------------------------------------------

def _collect_extras_at(dst_fd, rel, ctx, extras):
    """Collect destination entries that have no counterpart in the source.

    Extra directories are captured whole and not descended into; a
    symlink is captured as a plain entry so it is unlinked rather than
    walked. Subtrees whose source was unreadable are left alone.
    """
    try:
        names = dirfd.listdir_at(dst_fd)
    except OSError:
        return

    for name in names:
        child = _rel(rel, name)
        if child in ctx.skipped_rels:
            continue
        try:
            st = dirfd.lstat_at(dst_fd, name)
        except OSError:
            continue
        is_link = stat.S_ISLNK(st.st_mode)
        is_dir = stat.S_ISDIR(st.st_mode)

        if child not in ctx.src_rels:
            extras.append((child, is_dir and not is_link))
        elif is_dir and not is_link:
            try:
                sub = dirfd.opendir_at(dst_fd, name)
            except OSError:
                continue
            try:
                _collect_extras_at(sub, child, ctx, extras)
            finally:
                os.close(sub)


def _remove_extras_at(dst_fd, rel, targets, ctx, counter):
    """Delete the entries named in *targets*, walking by fd."""
    try:
        names = dirfd.listdir_at(dst_fd)
    except OSError:
        return

    for name in names:
        child = _rel(rel, name)
        if child in targets:
            counter[0] += 1
            if ctx.verbose:
                log_info(f"({counter[0]}/{counter[1]}) Delete: "
                         f"{os.path.join(ctx.dest_root, child)}")
            _unlink_robust(dst_fd, name, targets[child])
            continue
        if child in ctx.skipped_rels:
            continue
        try:
            st = dirfd.lstat_at(dst_fd, name)
        except OSError:
            continue
        if stat.S_ISDIR(st.st_mode) and not stat.S_ISLNK(st.st_mode):
            try:
                sub = dirfd.opendir_at(dst_fd, name)
            except OSError:
                continue
            try:
                _remove_extras_at(sub, child, targets, ctx, counter)
            finally:
                os.close(sub)


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
    src_path = resolve_container_path(src)
    dest_path = resolve_container_path(dest)

    try:
        src_st = os.lstat(src_path)
    except OSError:
        crit_error(f"source path '{src}' does not exist.")
        sys.exit(1)

    src_is_dir = stat.S_ISDIR(src_st.st_mode)

    if src_is_dir and not os.access(src_path, os.R_OK | os.X_OK):
        crit_error(f"source directory '{src}' is not readable.")
        sys.exit(1)

    # If src is a file and dest is an existing dir, place file inside it.
    if not src_is_dir and os.path.isdir(dest_path):
        dest_path = os.path.join(dest_path, os.path.basename(src_path))

    log_info("Synchronizing files...")
    log_info(f"Source: '{src_path}'")
    log_info(f"Destination: '{dest_path}'")

    if src_is_dir:
        try:
            os.makedirs(dest_path, exist_ok=True)
        except OSError as exc:
            log_error(f"Cannot create destination '{dest_path}': {exc}")
            sys.exit(1)

    ctx = _Ctx(src_path, dest, dest_path, verbose, use_checksum)

    # Pin both roots. inside=True for a directory sync: everything is
    # written *underneath* the root, so the root's own name must be
    # covered too — a root that became a symlink is refused, not
    # followed. The pins are held for the whole transfer.
    with ExitStack() as pins:
        src_pin = pins.enter_context(pin_path(src, src_path,
                                              inside=src_is_dir))
        dest_pin = pins.enter_context(pin_path(dest, dest_path,
                                               inside=src_is_dir))
        try:
            if src_is_dir:
                _sync_directory(src_pin, dest_pin, ctx, delete)
            else:
                _sync_single(src_pin, dest_pin, src_st, ctx)
        except KeyboardInterrupt:
            clear_bar()
            log_error("Aborted by user.")
            sys.exit(1)

    clear_bar()
    log_info("Finished synchronizing.")


def _sync_single(src_pin, dest_pin, src_st, ctx):
    """Sync a source that is a single file or symlink."""
    mode = src_st.st_mode
    if _is_special(mode):
        return
    if stat.S_ISLNK(mode):
        _sync_symlink(src_pin.dir_fd, src_pin.leaf,
                      dest_pin.dir_fd, dest_pin.leaf, src_st)
        return
    if not stat.S_ISREG(mode):
        return
    if _needs_update(src_pin.dir_fd, src_pin.leaf, src_st,
                     dest_pin.dir_fd, dest_pin.leaf, ctx.use_checksum):
        _sync_file(src_pin.dir_fd, src_pin.leaf, src_st,
                   dest_pin.dir_fd, dest_pin.leaf)


def _sync_directory(src_pin, dest_pin, ctx, delete):
    """Sync a directory source: count, mirror, then optionally prune."""
    src_fd = dirfd.reopen(src_pin.dir_fd, src_pin.leaf)
    try:
        dst_fd = dirfd.reopen(dest_pin.dir_fd, dest_pin.leaf)
        try:
            _collect_rels(src_fd, "", ctx)
            ctx.total = max(len(ctx.src_rels), 1)

            _mirror_at(src_fd, dst_fd, "", ctx)
            clear_bar()

            if delete:
                extras = []
                _collect_extras_at(dst_fd, "", ctx, extras)
                targets = dict(extras)
                counter = [0, len(extras)]
                _remove_extras_at(dst_fd, "", targets, ctx, counter)
        finally:
            os.close(dst_fd)
    finally:
        os.close(src_fd)
