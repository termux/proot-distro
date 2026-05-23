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

# Architecture: Creates a TAR archive of an installed proot container.
# Archive structure is <name>/manifest.json + <name>/rootfs/... so that
# restore can faithfully reconstruct the container directory. Compression
# is determined by file extension or by --compress flag. Progress is
# written to stderr so it doesn't corrupt piped archive data on stdout.

import os
import stat
import sys
import tarfile

from proot_distro.l2s import resolve_l2s_target
from proot_distro.message import log_info, log_error, crit_error
from proot_distro.progress import (
    REDRAW_THRESHOLD_BYTES, clear_bar, draw_bytes_bar,
)
from proot_distro.locking import ContainerLock
from proot_distro.names import require_valid_name
from proot_distro.paths import container_manifest, container_rootfs


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


def _iter_entries(root: str, arcroot: str, skip_top_level=()):
    """Yield *(src_path, arcname)* for every entry under *root*.

    Symlinks to subdirectories are yielded as single entries; os.walk is
    prevented from descending into them. All sibling entries are sorted.

    *skip_top_level* is a collection of directory names that should be
    omitted when they appear directly under *root* (no descent, no
    yielded entry). Used to exclude proot's `.l2s/` backing store
    whose contents are inlined into symlinks elsewhere in the tree.
    """
    skip = set(skip_top_level)
    for dirpath, dirnames, filenames in os.walk(
        root, followlinks=False, topdown=True
    ):
        rel = os.path.relpath(dirpath, root)
        if rel == '.' and skip:
            dirnames[:] = [d for d in dirnames if d not in skip]
        # Sort up front so every sibling — symlink-to-dir yields below,
        # subsequent descents, and file yields further down — comes out
        # in deterministic order.
        dirnames.sort()
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
    tf: tarfile.TarFile, src: str, arcname: str,
    rootfs: str, on_read=None,
) -> None:
    """Add *src* to *tf* as *arcname*, stripping ownership info.

    Block/character devices, FIFOs, and sockets are silently skipped.
    Symlinks are stored as symlinks (not followed) unless they are
    proot link2symlink emulated hard links — i.e. symlinks whose target
    basename matches the link2symlink prefix (see resolve_l2s_target).
    Those are resolved to the backing file's content and packed as
    regular files so the archive is self-contained and survives being
    restored to a different path. Regular files and directories are
    stored with their permissions intact.

    *rootfs* is the container's rootfs root, used to confine resolved
    l2s targets to the rootfs subtree.

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

    # Detect proot link2symlink symlinks (regardless of whether their
    # intermediate is stashed in <rootfs>/.l2s/ or alongside the
    # original) and pack their backing files' content as regular
    # files. Multiple l2s symlinks sharing one backing file become
    # independent regular files in the archive — the guest-side
    # hard-link semantics are lost, file content is preserved, and
    # the archive carries no absolute paths into the source rootfs
    # that would dangle after restore.
    if stat.S_ISLNK(m):
        try:
            target = os.readlink(src)
        except OSError:
            target = None
        if target is not None:
            l2s_path = resolve_l2s_target(src, target, rootfs)
            if l2s_path is not None:
                try:
                    cst = os.stat(l2s_path)
                except OSError:
                    cst = None
                if cst is not None and stat.S_ISREG(cst.st_mode):
                    info = tarfile.TarInfo(arcname)
                    info.type = tarfile.REGTYPE
                    info.size = cst.st_size
                    info.mode = stat.S_IMODE(cst.st_mode)
                    info.mtime = int(cst.st_mtime)
                    info.uid = 0
                    info.gid = 0
                    info.uname = ''
                    info.gname = ''
                    try:
                        with open(l2s_path, 'rb') as fh:
                            tf.addfile(
                                info,
                                _ReadCounter(fh, on_read) if on_read else fh,
                            )
                    except OSError:
                        pass
                    return
                # Backing file missing or non-regular: fall through and
                # store the symlink as-is.

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


def command_backup(args) -> None:
    """Archive an installed container to a tar file or stdout."""
    container_name = args.container_name
    output_path = getattr(args, "output", None)
    compression_arg = getattr(args, "compression", None)
    verbose = getattr(args, "verbose", False)

    require_valid_name(container_name)

    rootfs_dir = container_rootfs(container_name)
    manifest_path = container_manifest(container_name)

    if not os.path.isdir(rootfs_dir):
        crit_error(f"container '{container_name}' does not exist.")
        sys.exit(1)

    if output_path is not None and not output_path:
        crit_error("output file path cannot be empty.")
        sys.exit(1)

    if output_path:
        if os.path.isdir(output_path):
            crit_error(f"cannot write to "
                       f"'{output_path}' because this path is a directory.")
            sys.exit(1)
        if os.path.isfile(output_path):
            crit_error(f"file '{output_path}' already "
                       f"exists. Please specify a different name.")
            sys.exit(1)
        if compression_arg is not None:
            compression = _COMPRESSION_ARG_MAP[compression_arg]
        else:
            try:
                compression = _compression_mode(output_path)
            except ValueError as exc:
                crit_error(str(exc).lower())
                sys.exit(1)
    else:
        if sys.stdout.isatty():
            crit_error(f"archive data cannot be printed to "
                       f"console. Please use option '--output' to "
                       f"specify a file or pipe the output to "
                       f"another command.")
            sys.exit(1)
        compression = (
            _COMPRESSION_ARG_MAP[compression_arg]
            if compression_arg is not None
            else ''
        )

    with ContainerLock(container_name, exclusive=False, command="backup"):
        _run_backup(
            container_name, rootfs_dir, manifest_path,
            output_path, compression, verbose,
        )


def _run_backup(
    container_name, rootfs_dir, manifest_path,
    output_path, compression, verbose,
):
    log_info(f"Backing up '{container_name}'...")

    if output_path:
        log_info(f"Will write backup data to '{output_path}'.")
    else:
        log_info("Will write backup data to stdout.")

    log_info("Fixing file permissions in rootfs...")
    _fix_permissions(rootfs_dir)

    # Build the list of entries: manifest.json first, then rootfs tree.
    # Archive prefix is just the container name (e.g. "ubuntu/").
    # `.l2s` is skipped at the rootfs root because its files are inlined
    # into their referring symlinks by _add_path.
    arc_prefix = container_name
    entries = []
    # manifest.json (optional — may not exist for legacy containers).
    if os.path.isfile(manifest_path):
        entries.append((manifest_path, os.path.join(arc_prefix, "manifest.json")))
    # rootfs tree.
    entries.extend(_iter_entries(
        rootfs_dir, os.path.join(arc_prefix, "rootfs"),
        skip_top_level=(".l2s",),
    ))

    # Pre-compute total size of payload bytes to drive the progress bar.
    # Regular files contribute their own size; l2s symlinks contribute
    # the size of their backing file (since _add_path will inline that
    # content in place of the symlink).
    total_size = 0
    for src, _arc in entries:
        try:
            st = os.lstat(src)
        except OSError:
            continue
        if stat.S_ISREG(st.st_mode):
            total_size += st.st_size
        elif stat.S_ISLNK(st.st_mode):
            try:
                target = os.readlink(src)
            except OSError:
                continue
            l2s_path = resolve_l2s_target(src, target, rootfs_dir)
            if l2s_path is None:
                continue
            try:
                cst = os.stat(l2s_path)
            except OSError:
                continue
            if stat.S_ISREG(cst.st_mode):
                total_size += cst.st_size

    done_size = 0

    log_info("Archiving the container...")

    # Redraw threshold: update the bar at most once per 256 KiB read so
    # the _ReadCounter callback doesn't cause excessive stderr writes.
    _last_shown = 0

    def _draw_bar() -> None:
        nonlocal _last_shown
        draw_bytes_bar(done_size, total_size)
        _last_shown = done_size

    def _on_read(n: int) -> None:
        nonlocal done_size
        done_size += n
        if done_size - _last_shown >= REDRAW_THRESHOLD_BYTES:
            _draw_bar()

    def _on_entry(arc: str) -> None:
        if verbose:
            log_info(f"Adding: '{arc}'")
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
                _add_path(tf, src, arc, rootfs_dir, on_read=_on_read)
                _on_entry(arc)

        clear_bar()
        log_info("Finished backing up.")

    except KeyboardInterrupt:
        clear_bar()
        log_error("Aborted by user.")
        if output_path:
            try:
                os.remove(output_path)
            except OSError:
                pass
        sys.exit(1)
    except (OSError, tarfile.TarError) as exc:
        clear_bar()
        log_error(f"Failed to create backup archive: {exc}")
        if output_path:
            try:
                os.remove(output_path)
            except OSError:
                pass
        sys.exit(1)
