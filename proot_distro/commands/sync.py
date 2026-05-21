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
# special files (block/char/FIFO/socket) are silently skipped. Ownership
# is never changed. Modes and timestamps are preserved. When the destination
# lacks write permission the command attempts to chmod it; failing that it
# exits with an error. With --delete, destination entries that have no
# counterpart in the source are removed after the sync pass. Paths may be
# plain host paths or container-prefixed ('ubuntu:/etc') references.

import os
import shutil
import stat
import sys
from contextlib import ExitStack

from proot_distro.message import log_info, log_error, crit_error
from proot_distro.paths import (
    container_locks_for_spec_pair, resolve_container_path,
)
from proot_distro.progress import clear_bar, draw_count_bar


def _file_checksum(path: str) -> int:
    """Return a CRC32 checksum of the file at path."""
    import zlib
    crc = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            crc = zlib.crc32(chunk, crc)
    return crc


def _needs_update(
    src_path: str,
    src_st: os.stat_result,
    dst_path: str,
    use_checksum: bool,
) -> bool:
    """Return True when dst must be (re)written from src.

    Comparison logic for regular files only:
    - Always update on size mismatch.
    - With --checksum: compare CRC32 digests.
    - Without --checksum: compare integer modification times.
    """
    try:
        dst_st = os.lstat(dst_path)
    except OSError:
        return True
    if stat.S_IFMT(src_st.st_mode) != stat.S_IFMT(dst_st.st_mode):
        return True
    if src_st.st_size != dst_st.st_size:
        return True
    if use_checksum:
        try:
            return _file_checksum(src_path) != _file_checksum(dst_path)
        except OSError:
            return True
    return int(src_st.st_mtime) != int(dst_st.st_mtime)


def _ensure_parent_writable(path: str) -> None:
    """Ensure the parent directory of path is writable. Exits on failure."""
    parent = os.path.dirname(path) or "."
    try:
        st = os.stat(parent)
        if not (st.st_mode & stat.S_IWUSR):
            os.chmod(parent, st.st_mode | stat.S_IWUSR | stat.S_IXUSR)
    except OSError as exc:
        log_error(f"Cannot make '{parent}' writable: {exc}")
        sys.exit(1)


def _ensure_writable(path: str) -> None:
    """Ensure path itself is writable. Exits on failure."""
    try:
        st = os.lstat(path)
        if not (st.st_mode & stat.S_IWUSR):
            os.chmod(path, st.st_mode | stat.S_IWUSR)
    except OSError as exc:
        log_error(f"Cannot set write permission on '{path}': {exc}")
        sys.exit(1)


def _sync_dir(dst_path: str, src_st: os.stat_result) -> bool:
    """Create dst_path as a directory if it does not exist.

    Returns True when the directory was newly created.
    """
    if os.path.isdir(dst_path):
        try:
            os.chmod(dst_path, stat.S_IMODE(src_st.st_mode))
        except OSError:
            pass
        return False

    try:
        os.makedirs(dst_path, exist_ok=True)
    except PermissionError:
        _ensure_parent_writable(dst_path)
        try:
            os.makedirs(dst_path, exist_ok=True)
        except OSError as exc:
            log_error(f"Cannot create directory '{dst_path}': {exc}")
            sys.exit(1)
    except OSError as exc:
        log_error(f"Cannot create directory '{dst_path}': {exc}")
        sys.exit(1)

    try:
        os.chmod(dst_path, stat.S_IMODE(src_st.st_mode))
    except OSError:
        pass
    return True


def _sync_symlink(src_path: str, dst_path: str) -> bool:
    """Copy symlink src_path to dst_path as-is.

    Returns True when dst was created or updated.
    """
    target = os.readlink(src_path)

    if os.path.lexists(dst_path):
        if os.path.islink(dst_path) and os.readlink(dst_path) == target:
            return False
        try:
            os.unlink(dst_path)
        except PermissionError:
            _ensure_parent_writable(dst_path)
            try:
                os.unlink(dst_path)
            except OSError as exc:
                log_error(f"Cannot remove existing path '{dst_path}': {exc}")
                sys.exit(1)
        except OSError as exc:
            log_error(f"Cannot remove existing path '{dst_path}': {exc}")
            sys.exit(1)

    try:
        os.symlink(target, dst_path)
    except PermissionError:
        _ensure_parent_writable(dst_path)
        try:
            os.symlink(target, dst_path)
        except OSError as exc:
            log_error(f"Cannot create symlink '{dst_path}': {exc}")
            sys.exit(1)
    except OSError as exc:
        log_error(f"Cannot create symlink '{dst_path}': {exc}")
        sys.exit(1)

    return True


def _sync_file(src_path: str, src_st: os.stat_result, dst_path: str) -> None:
    """Copy src_path to dst_path, preserving mode and mtime.

    Writes to a sibling temp file then atomically renames so a partial
    write never leaves dst_path in a corrupt state.
    """
    tmp = dst_path + ".~pd_sync"
    try:
        with open(src_path, "rb") as fin, open(tmp, "wb") as fout:
            shutil.copyfileobj(fin, fout)
    except PermissionError:
        _ensure_parent_writable(dst_path)
        if os.path.exists(dst_path):
            _ensure_writable(dst_path)
        try:
            with open(src_path, "rb") as fin, open(tmp, "wb") as fout:
                shutil.copyfileobj(fin, fout)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            log_error(f"Cannot write to '{dst_path}': {exc}")
            sys.exit(1)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        log_error(f"Cannot write to '{dst_path}': {exc}")
        sys.exit(1)

    try:
        os.chmod(tmp, stat.S_IMODE(src_st.st_mode))
    except OSError:
        pass
    try:
        os.utime(tmp, (src_st.st_atime, src_st.st_mtime))
    except OSError:
        pass

    try:
        os.replace(tmp, dst_path)
    except PermissionError:
        _ensure_writable(dst_path)
        try:
            os.replace(tmp, dst_path)
        except OSError as exc:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            log_error(f"Cannot replace '{dst_path}': {exc}")
            sys.exit(1)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        log_error(f"Cannot replace '{dst_path}': {exc}")
        sys.exit(1)


def _unlink_robust(path: str) -> None:
    """Unlink a file or symlink, retrying with a chmod on PermissionError."""
    try:
        os.unlink(path)
    except PermissionError:
        _ensure_parent_writable(path)
        try:
            os.unlink(path)
        except OSError as exc:
            log_error(f"Cannot delete '{path}': {exc}")
            sys.exit(1)
    except OSError as exc:
        log_error(f"Cannot delete '{path}': {exc}")
        sys.exit(1)


def _rmtree_robust(path: str) -> None:
    """Remove a directory tree, retrying with chmod on PermissionError."""
    try:
        shutil.rmtree(path)
    except PermissionError:
        for root, _dirs, files in os.walk(path, followlinks=False, topdown=False):
            try:
                os.chmod(root, os.stat(root).st_mode | stat.S_IRWXU)
            except OSError:
                pass
            for fname in files:
                try:
                    fpath = os.path.join(root, fname)
                    os.chmod(fpath,
                             os.stat(fpath).st_mode | stat.S_IRUSR | stat.S_IWUSR)
                except OSError:
                    pass
        try:
            shutil.rmtree(path)
        except OSError as exc:
            log_error(f"Cannot remove '{path}': {exc}")
            sys.exit(1)
    except OSError as exc:
        log_error(f"Cannot remove '{path}': {exc}")
        sys.exit(1)


def _collect_extras(
    dest_path: str,
    src_rels: set,
    skipped_src_rels: set = frozenset(),
) -> list:
    """Return (path, is_tree) for every destination entry absent from src_rels.

    is_tree is True for real directories (to be removed recursively) and
    False for plain files and symlinks. Extra directories are not descended
    into — the whole subtree is captured as a single is_tree=True entry.
    Entries whose relative path is in skipped_src_rels are left untouched
    because the corresponding source directory was unreadable.
    """
    extras = []
    for dirpath, dirnames, filenames in os.walk(
        dest_path, followlinks=False, topdown=True
    ):
        rel_dir = os.path.relpath(dirpath, dest_path)
        dirnames.sort()
        i = 0
        while i < len(dirnames):
            d = dirnames[i]
            full = os.path.join(dirpath, d)
            rel_d = os.path.join(rel_dir, d) if rel_dir != "." else d
            is_link = os.path.islink(full)
            if rel_d in skipped_src_rels:
                dirnames.pop(i)
            elif rel_d not in src_rels:
                extras.append((full, not is_link))
                dirnames.pop(i)
            elif is_link:
                dirnames.pop(i)
            else:
                i += 1
        for fname in sorted(filenames):
            fpath = os.path.join(dirpath, fname)
            rel_f = os.path.join(rel_dir, fname) if rel_dir != "." else fname
            if rel_f not in src_rels:
                extras.append((fpath, False))
    return extras


def _collect_entries(src: str) -> tuple[list, set]:
    """Return (entries, skipped_rels).

    entries: (abs_path, rel_path) pairs for every syncable entry under src.
    rel_path is "" for the source root. Directories appear before their
    contents. Symlinks-to-directories are single entries. Block/char/FIFO/
    socket entries are excluded.

    skipped_rels: relative paths of subdirectories that could not be
    traversed (e.g. permission denied). A warning is emitted for each.
    """
    entries = []
    skipped_rels = set()

    try:
        src_st = os.lstat(src)
    except OSError:
        return entries, skipped_rels

    m = src_st.st_mode
    if stat.S_ISBLK(m) or stat.S_ISCHR(m) or stat.S_ISFIFO(m) or stat.S_ISSOCK(m):
        return entries, skipped_rels

    if not stat.S_ISDIR(m):
        entries.append((src, ""))
        return entries, skipped_rels

    def _on_error(exc: OSError) -> None:
        if exc.filename:
            rel = os.path.relpath(exc.filename, src)
            if rel != ".":
                skipped_rels.add(rel)
                log_error(
                    f"Warning: directory '{exc.filename}' is not readable, "
                    f"skipping."
                )

    for dirpath, dirnames, filenames in os.walk(
        src, followlinks=False, topdown=True, onerror=_on_error
    ):
        rel = os.path.relpath(dirpath, src)

        if rel != ".":
            entries.append((dirpath, rel))

        dirnames.sort()
        i = 0
        while i < len(dirnames):
            d = dirnames[i]
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                link_rel = os.path.join(rel, d) if rel != "." else d
                entries.append((full, link_rel))
                dirnames.pop(i)
            else:
                i += 1

        for fname in sorted(filenames):
            fpath = os.path.join(dirpath, fname)
            file_rel = os.path.join(rel, fname) if rel != "." else fname
            entries.append((fpath, file_rel))

    return entries, skipped_rels


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
        except PermissionError:
            _ensure_parent_writable(dest_path)
            try:
                os.makedirs(dest_path, exist_ok=True)
            except OSError as exc:
                log_error(f"Cannot create destination '{dest_path}': {exc}")
                sys.exit(1)
        except OSError as exc:
            log_error(f"Cannot create destination '{dest_path}': {exc}")
            sys.exit(1)

    entries, skipped_rels = _collect_entries(src_path)
    total = max(len(entries), 1)
    done = 0

    def _show_progress() -> None:
        # Suppress the bar in verbose mode: per-file log lines already
        # provide feedback and the bar would flicker between each line.
        if verbose:
            return
        draw_count_bar(done, total, unit="files")

    try:
        for abs_path, rel_path in entries:
            try:
                item_st = os.lstat(abs_path)
            except OSError as exc:
                log_error(f"Warning: cannot stat '{abs_path}': {exc}")
                done += 1
                _show_progress()
                continue

            m = item_st.st_mode

            if (stat.S_ISBLK(m) or stat.S_ISCHR(m)
                    or stat.S_ISFIFO(m) or stat.S_ISSOCK(m)):
                done += 1
                _show_progress()
                continue

            dst_item = (
                os.path.join(dest_path, rel_path) if rel_path else dest_path
            )

            if stat.S_ISDIR(m):
                created = _sync_dir(dst_item, item_st)
                if verbose and created:
                    log_info(f"({done + 1}/{total}) New directory: "
                             f"{os.path.join(dest,rel_path)}")

            elif stat.S_ISLNK(m):
                op = "Modified" if os.path.lexists(dst_item) else "New"
                changed = _sync_symlink(abs_path, dst_item)
                if verbose and changed:
                    log_info(f"({done + 1}/{total}) {op} symlink: "
                             f"{os.path.join(dest,rel_path)}")

            elif stat.S_ISREG(m):
                if not os.access(abs_path, os.R_OK):
                    log_error(f"Warning: file '{abs_path}' is not readable, "
                              f"skipping.")
                elif _needs_update(abs_path, item_st, dst_item, use_checksum):
                    op = "Modified" if os.path.lexists(dst_item) else "New"
                    _sync_file(abs_path, item_st, dst_item)
                    if verbose:
                        log_info(f"({done + 1}/{total}) {op} file: "
                                 f"{os.path.join(dest,rel_path)}")

            done += 1
            _show_progress()

    except KeyboardInterrupt:
        log_error("Aborted by user.")
        sys.exit(1)

    clear_bar()

    # --delete pass: remove destination entries absent from the source.
    # Only meaningful when source is a directory; silently skipped otherwise.
    if delete and src_is_dir:
        src_rels = {rel for _, rel in entries if rel}
        extras = _collect_extras(dest_path, src_rels, skipped_rels)
        del_total = len(extras)
        for del_n, (path, is_tree) in enumerate(extras, start=1):
            if verbose:
                log_info(f"({del_n}/{del_total}) Delete: {path}")
            if is_tree:
                _rmtree_robust(path)
            else:
                _unlink_robust(path)

    log_info("Finished synchronizing.")
