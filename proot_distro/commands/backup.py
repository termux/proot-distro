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

# Architecture: Creates a TAR archive of an installed proot container.
# Archive structure is <name>/manifest.json + <name>/rootfs/... so that
# restore can faithfully reconstruct the container directory. Compression
# is determined by file extension or by --compress flag. Progress is
# written to stderr so it doesn't corrupt piped archive data on stdout.

import os
import stat
import sys
import tarfile

from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.colors import C, msg
from proot_distro.helpers.download import fmt_size
from proot_distro.commands.help import _HELP_COMMANDS


# Maps file-extension suffixes to tarfile compression identifiers.
_COMPRESS_EXTS = (
    ('.tar.gz',   'gz'),
    ('.tgz',      'gz'),
    ('.tar.bz2',  'bz2'),
    ('.tbz2',     'bz2'),
    ('.tar.xz',   'xz'),
    ('.txz',      'xz'),
    ('.tar.lzma', 'xz'),
    ('.tlzma',    'xz'),
    ('.tar',      ''),
)

# Extensions that look like compression requests but are not supported.
_UNSUPPORTED_EXTS = ('.tar.zst', '.tzst', '.tar.lz4', '.tar.lz')

# Maps --compress argument values to tarfile compression identifiers.
_COMPRESSION_ARG_MAP = {
    'gzip':  'gz',
    'bzip2': 'bz2',
    'xz':    'xz',
    'none':  '',
}


def _compression_mode(filename: str) -> str:
    """Return the tarfile compression suffix for *filename*'s extension.

    Raises ValueError for recognised-but-unsupported formats.
    Falls back to uncompressed ('') for unknown extensions.
    """
    low = filename.lower()
    for ext, comp in _COMPRESS_EXTS:
        if low.endswith(ext):
            return comp
    for ext in _UNSUPPORTED_EXTS:
        if low.endswith(ext):
            raise ValueError(f"Compression format '{ext}' is not supported.")
    return ''


def _iter_entries(root: str, arcroot: str):
    """Yield *(src_path, arcname)* for every entry under *root*.

    Symlinks to subdirectories are yielded as single entries; os.walk is
    prevented from descending into them. All sibling entries are sorted.
    """
    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, topdown=True
    ):
        rel = os.path.relpath(dirpath, root)
        arc_dir = arcroot if rel == '.' else os.path.join(arcroot, rel)

        yield (dirpath, arc_dir)

        i = 0
        while i < len(dirnames):
            d = dirnames[i]
            if os.path.islink(os.path.join(dirpath, d)):
                yield (os.path.join(dirpath, d), os.path.join(arc_dir, d))
                dirnames.pop(i)
            else:
                i += 1

        dirnames.sort()

        for fname in sorted(filenames):
            yield (os.path.join(dirpath, fname), os.path.join(arc_dir, fname))


class _ReadCounter:
    """File wrapper that calls on_read(n) with the byte count after each read.

    Used to stream progress updates through tarfile's internal copy loop so
    the bar advances during compression rather than only between files.
    """

    def __init__(self, fh, on_read):
        self._fh = fh
        self._on_read = on_read

    def read(self, n=-1):
        data = self._fh.read(n)
        if data:
            self._on_read(len(data))
        return data

    def __getattr__(self, name):
        return getattr(self._fh, name)


def _add_path(
    tf: tarfile.TarFile, src: str, arcname: str, on_read=None
) -> None:
    """Add *src* to *tf* as *arcname*, stripping ownership info.

    Block/character devices, FIFOs, and sockets are silently skipped.
    Symlinks are stored as symlinks (not followed). Regular files and
    directories are stored with their permissions intact.

    *on_read*, when provided, is called with the byte count of each chunk
    read from a regular file so callers can track progress during compression.
    """
    try:
        st = os.lstat(src)
    except OSError:
        return
    m = st.st_mode
    if (stat.S_ISBLK(m) or stat.S_ISCHR(m)
            or stat.S_ISFIFO(m) or stat.S_ISSOCK(m)):
        return
    try:
        info = tf.gettarinfo(src, arcname=arcname)
    except OSError:
        return
    info.uid = 0
    info.gid = 0
    info.uname = ''
    info.gname = ''
    if stat.S_ISREG(m):
        try:
            with open(src, 'rb') as fh:
                tf.addfile(info, _ReadCounter(fh, on_read) if on_read else fh)
        except OSError:
            pass
    else:
        tf.addfile(info)  # directories and symlinks carry no data stream


def _fix_permissions(rootfs_dir: str) -> None:
    """Ensure all dirs and files in *rootfs_dir* are readable by owner."""
    for dirpath, _dirs, files in os.walk(rootfs_dir):
        try:
            os.chmod(
                dirpath,
                os.stat(dirpath).st_mode | stat.S_IRUSR | stat.S_IXUSR,
            )
        except OSError:
            pass
        for fname in files:
            fpath = os.path.join(dirpath, fname)
            try:
                fst = os.lstat(fpath)
                if stat.S_ISREG(fst.st_mode):
                    mode = fst.st_mode
                    if mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
                        os.chmod(fpath, mode | stat.S_IRUSR | stat.S_IXUSR)
                    else:
                        os.chmod(fpath, mode | stat.S_IRUSR)
            except OSError:
                pass


def command_backup(args, configs: dict) -> None:  # noqa: ARG001
    dist_name = args.alias
    output_path = getattr(args, "output", None)
    compression_arg = getattr(args, "compression", None)
    verbose = getattr(args, "verbose", False)

    container_dir = os.path.join(CONTAINERS_DIR, dist_name)
    rootfs_dir = os.path.join(container_dir, "rootfs")
    manifest_path = os.path.join(container_dir, "manifest.json")

    if not os.path.isdir(rootfs_dir):
        msg()
        msg(f"{C['BRED']}Error: container "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}' does not exist.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}You can create it with: "
            f"{C['GREEN']}{PROGRAM_NAME} install {dist_name}{C['RST']}")
        msg()
        sys.exit(1)

    if output_path:
        if os.path.isdir(output_path):
            msg()
            msg(f"{C['BRED']}Error: cannot write to "
                f"'{C['YELLOW']}{output_path}{C['BRED']}' because this path "
                f"is a directory.{C['RST']}")
            _HELP_COMMANDS["backup"]()
            sys.exit(1)
        if os.path.isfile(output_path):
            msg()
            msg(f"{C['BRED']}Error: file "
                f"'{C['YELLOW']}{output_path}{C['BRED']}' already exists. "
                f"Please specify a different name.{C['RST']}")
            _HELP_COMMANDS["backup"]()
            sys.exit(1)
        if compression_arg is not None:
            compression = _COMPRESSION_ARG_MAP[compression_arg]
        else:
            try:
                compression = _compression_mode(output_path)
            except ValueError as exc:
                msg()
                msg(f"{C['BRED']}Error: {exc}{C['RST']}")
                msg()
                sys.exit(1)
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Tarball will be written to '{output_path}'.{C['RST']}")
    else:
        if sys.stdout.isatty():
            msg()
            msg(f"{C['BRED']}Error: archive data cannot be printed to "
                f"console. Please use option "
                f"'{C['YELLOW']}--output{C['BRED']}' to specify a file or "
                f"pipe the output to another command.{C['RST']}")
            msg()
            sys.exit(1)
        compression = (
            _COMPRESSION_ARG_MAP[compression_arg]
            if compression_arg is not None
            else ''
        )
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Tarball will be written to stdout.{C['RST']}")

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Backing up '{C['YELLOW']}{dist_name}{C['CYAN']}'...{C['RST']}")

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Fixing file permissions in rootfs...{C['RST']}")
    _fix_permissions(rootfs_dir)

    # Build the list of entries: manifest.json first, then rootfs tree.
    # Archive prefix is just the container name (e.g. "ubuntu/").
    arc_prefix = dist_name
    entries = []
    # manifest.json (optional — may not exist for legacy containers).
    if os.path.isfile(manifest_path):
        entries.append((manifest_path, os.path.join(arc_prefix, "manifest.json")))
    # rootfs tree.
    entries.extend(_iter_entries(rootfs_dir, os.path.join(arc_prefix, "rootfs")))

    # Pre-compute total size of regular files to drive the progress bar.
    total_size = 0
    for src, _arc in entries:
        try:
            st = os.lstat(src)
            if stat.S_ISREG(st.st_mode):
                total_size += st.st_size
        except OSError:
            pass

    done_size = 0
    use_tty = sys.stderr.isatty()

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Archiving the container...{C['RST']}")

    # Redraw threshold: update the bar at most once per 256 KiB read so the
    # _ReadCounter callback doesn't cause excessive stderr writes.
    _last_shown = 0

    def _draw_bar() -> None:
        nonlocal _last_shown
        if not use_tty:
            return
        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        pct = done_size * 100 // total_size if total_size else 100
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        sys.stderr.write(
            f"\r{pfx}[{bar}] {pct:3d}%  "
            f"{fmt_size(done_size)} / {fmt_size(total_size)}\033[K{C['RST']}"
        )
        sys.stderr.flush()
        _last_shown = done_size

    def _on_read(n: int) -> None:
        nonlocal done_size
        done_size += n
        if done_size - _last_shown >= 262144:
            _draw_bar()

    def _on_entry(arc: str) -> None:
        if verbose:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Adding: '{arc}'{C['RST']}")
        _draw_bar()

    try:
        tar_mode = f'w:{compression}' if output_path else f'w|{compression}'
        tar_target = output_path if output_path else sys.stdout.buffer

        with tarfile.open(
            tar_target if output_path else None,
            fileobj=None if output_path else tar_target,
            mode=tar_mode,
        ) as tf:
            for src, arc in entries:
                _add_path(tf, src, arc, on_read=_on_read)
                _on_entry(arc)

        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Finished backing up.{C['RST']}")

    except KeyboardInterrupt:
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Aborted by user.{C['RST']}")
        if output_path:
            try:
                os.remove(output_path)
            except OSError:
                pass
        sys.exit(1)
    except (OSError, tarfile.TarError) as exc:
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Failed to create archive: {exc}{C['RST']}")
        if output_path:
            try:
                os.remove(output_path)
            except OSError:
                pass
        sys.exit(1)
