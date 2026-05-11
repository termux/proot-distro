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

import hashlib
import os
import shutil
import stat
import sys

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.colors import C, msg


def _resolve_sync_path(spec: str) -> str:
    """Resolve a 'dist:path' or plain host path to an absolute host path."""
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
        msg()
        msg(f"{C['BRED']}Error: cannot make '{parent}' writable: "
            f"{exc}{C['RST']}")
        msg()
        sys.exit(1)


def _ensure_writable(path: str) -> None:
    """Ensure path itself is writable. Exits on failure."""
    try:
        st = os.lstat(path)
        if not (st.st_mode & stat.S_IWUSR):
            os.chmod(path, st.st_mode | stat.S_IWUSR)
    except OSError as exc:
        msg()
        msg(f"{C['BRED']}Error: cannot set write permission on "
            f"'{path}': {exc}{C['RST']}")
        msg()
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
            msg()
            msg(f"{C['BRED']}Error: cannot create directory "
                f"'{dst_path}': {exc}{C['RST']}")
            msg()
            sys.exit(1)
    except OSError as exc:
        msg()
        msg(f"{C['BRED']}Error: cannot create directory "
            f"'{dst_path}': {exc}{C['RST']}")
        msg()
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
                msg()
                msg(f"{C['BRED']}Error: cannot remove existing path "
                    f"'{dst_path}': {exc}{C['RST']}")
                msg()
                sys.exit(1)
        except OSError as exc:
            msg()
            msg(f"{C['BRED']}Error: cannot remove existing path "
                f"'{dst_path}': {exc}{C['RST']}")
            msg()
            sys.exit(1)

    try:
        os.symlink(target, dst_path)
    except PermissionError:
        _ensure_parent_writable(dst_path)
        try:
            os.symlink(target, dst_path)
        except OSError as exc:
            msg()
            msg(f"{C['BRED']}Error: cannot create symlink "
                f"'{dst_path}': {exc}{C['RST']}")
            msg()
            sys.exit(1)
    except OSError as exc:
        msg()
        msg(f"{C['BRED']}Error: cannot create symlink "
            f"'{dst_path}': {exc}{C['RST']}")
        msg()
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
            msg()
            msg(f"{C['BRED']}Error: cannot write to "
                f"'{dst_path}': {exc}{C['RST']}")
            msg()
            sys.exit(1)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        msg()
        msg(f"{C['BRED']}Error: cannot write to "
            f"'{dst_path}': {exc}{C['RST']}")
        msg()
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
            msg()
            msg(f"{C['BRED']}Error: cannot replace "
                f"'{dst_path}': {exc}{C['RST']}")
            msg()
            sys.exit(1)
    except OSError as exc:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        msg()
        msg(f"{C['BRED']}Error: cannot replace "
            f"'{dst_path}': {exc}{C['RST']}")
        msg()
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
            msg()
            msg(f"{C['BRED']}Error: cannot delete "
                f"'{C['YELLOW']}{path}{C['BRED']}': {exc}{C['RST']}")
            msg()
            sys.exit(1)
    except OSError as exc:
        msg()
        msg(f"{C['BRED']}Error: cannot delete "
            f"'{C['YELLOW']}{path}{C['BRED']}': {exc}{C['RST']}")
        msg()
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
            msg()
            msg(f"{C['BRED']}Error: cannot remove "
                f"'{C['YELLOW']}{path}{C['BRED']}': {exc}{C['RST']}")
            msg()
            sys.exit(1)
    except OSError as exc:
        msg()
        msg(f"{C['BRED']}Error: cannot remove "
            f"'{C['YELLOW']}{path}{C['BRED']}': {exc}{C['RST']}")
        msg()
        sys.exit(1)


def _collect_extras(dest_path: str, src_rels: set) -> list:
    """Return (path, is_tree) for every destination entry absent from src_rels.

    is_tree is True for real directories (to be removed recursively) and
    False for plain files and symlinks. Extra directories are not descended
    into — the whole subtree is captured as a single is_tree=True entry.
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
            if rel_d not in src_rels:
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


def _collect_entries(src: str) -> list:
    """Return (abs_path, rel_path) pairs for every syncable entry under src.

    rel_path is "" when the entry is the source root (file or directory).
    Directories appear before their contents. Symlinks-to-directories are
    yielded as single entries; os.walk does not descend into them.
    Block/char devices, FIFOs, and sockets are excluded.
    """
    entries = []
    try:
        src_st = os.lstat(src)
    except OSError:
        return entries

    m = src_st.st_mode
    if stat.S_ISBLK(m) or stat.S_ISCHR(m) or stat.S_ISFIFO(m) or stat.S_ISSOCK(m):
        return entries

    if not stat.S_ISDIR(m):
        entries.append((src, ""))
        return entries

    for dirpath, dirnames, filenames in os.walk(src, followlinks=False, topdown=True):
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

    return entries


def command_sync(args, configs: dict) -> None:  # noqa: ARG001
    src = args.source
    dest = args.destination
    verbose = getattr(args, "verbose", False)
    use_checksum = getattr(args, "checksum", False)
    delete = getattr(args, "delete", False)

    src_path = _resolve_sync_path(src)
    dest_path = _resolve_sync_path(dest)

    try:
        src_st = os.lstat(src_path)
    except OSError:
        msg()
        msg(f"{C['BRED']}Error: cannot access source "
            f"'{C['YELLOW']}{src}{C['BRED']}': path does not exist.{C['RST']}")
        msg()
        sys.exit(1)

    src_is_dir = stat.S_ISDIR(src_st.st_mode)

    # If src is a file and dest is an existing dir, place file inside it.
    if not src_is_dir and os.path.isdir(dest_path):
        dest_path = os.path.join(dest_path, os.path.basename(src_path))

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Source:      '{src_path}'{C['RST']}")
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Destination: '{dest_path}'{C['RST']}")

    if src_is_dir:
        try:
            os.makedirs(dest_path, exist_ok=True)
        except PermissionError:
            _ensure_parent_writable(dest_path)
            try:
                os.makedirs(dest_path, exist_ok=True)
            except OSError as exc:
                msg()
                msg(f"{C['BRED']}Error: cannot create destination "
                    f"'{dest_path}': {exc}{C['RST']}")
                msg()
                sys.exit(1)
        except OSError as exc:
            msg()
            msg(f"{C['BRED']}Error: cannot create destination "
                f"'{dest_path}': {exc}{C['RST']}")
            msg()
            sys.exit(1)

    entries = _collect_entries(src_path)
    total = max(len(entries), 1)
    done = 0
    # Suppress the bar in verbose mode: log lines already provide per-file
    # feedback and the bar would flicker between every line, clogging output.
    use_tty = sys.stderr.isatty() and not verbose

    def _show_progress() -> None:
        if not use_tty:
            return
        pct = done * 100 // total
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        sys.stderr.write(
            f"\r{pfx}[{bar}] {pct:3d}%  {done} / {total} files\033[K{C['RST']}"
        )
        sys.stderr.flush()

    def _log(text: str) -> None:
        # The progress bar leaves the cursor at the end of its line with no
        # trailing newline. Clear the line before printing so that log
        # messages and the redrawn progress bar never mix on the same line.
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(text)

    try:
        for abs_path, rel_path in entries:
            try:
                item_st = os.lstat(abs_path)
            except OSError as exc:
                _log(f"{C['BLUE']}[{C['YELLOW']}!{C['BLUE']}] {C['CYAN']}"
                     f"Warning: cannot stat '{abs_path}': {exc}{C['RST']}")
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
                    _log(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                         f"({done + 1}/{total}) Add: "
                         f"{abs_path} -> {dst_item}{C['RST']}")

            elif stat.S_ISLNK(m):
                op = "Update" if os.path.lexists(dst_item) else "Add"
                changed = _sync_symlink(abs_path, dst_item)
                if verbose and changed:
                    _log(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                         f"({done + 1}/{total}) {op}: "
                         f"{abs_path} -> {dst_item}{C['RST']}")

            elif stat.S_ISREG(m):
                if not os.access(abs_path, os.R_OK):
                    _log(f"{C['BLUE']}[{C['YELLOW']}!{C['BLUE']}] {C['CYAN']}"
                         f"Warning: '{abs_path}' is not readable, "
                         f"skipping.{C['RST']}")
                elif _needs_update(abs_path, item_st, dst_item, use_checksum):
                    op = "Update" if os.path.lexists(dst_item) else "Add"
                    _sync_file(abs_path, item_st, dst_item)
                    if verbose:
                        _log(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                             f"({done + 1}/{total}) {op}: "
                             f"{abs_path} -> {dst_item}{C['RST']}")

            done += 1
            _show_progress()

    except KeyboardInterrupt:
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Aborted by user.{C['RST']}")
        sys.exit(1)

    if use_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()

    # --delete pass: remove destination entries absent from the source.
    # Only meaningful when source is a directory; silently skipped otherwise.
    if delete and src_is_dir:
        src_rels = {rel for _, rel in entries if rel}
        extras = _collect_extras(dest_path, src_rels)
        del_total = len(extras)
        for del_n, (path, is_tree) in enumerate(extras, start=1):
            if verbose:
                _log(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                     f"({del_n}/{del_total}) Delete: {path}{C['RST']}")
            if is_tree:
                _rmtree_robust(path)
            else:
                _unlink_robust(path)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Finished synchronizing.{C['RST']}")
