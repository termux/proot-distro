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
#
# The container directory is opened once -- through paths.open_container_dir(),
# the same O_NOFOLLOW walk the installed check makes -- and everything below
# it is named as (dir_fd, entry). The three passes used to reopen
# containers/<name> by path, which is guest-writable on Termux, so a session
# that re-pointed it after the check had the permission pass chmod, and the
# archiver pack, a host directory of its choosing under the container's name.
#
# The rootfs is walked through directory descriptors (see proot_distro.dirfd),
# never by path. `backup` holds only a *shared* lock, so a `login` session
# can be running against the same container while the archive is written,
# and every path-based step here was two acts on two possibly-different
# files: _fix_permissions() stat'ed a name and then chmod'ed it, and the
# archiver lstat'ed a name and then opened it. A guest that swaps a
# directory or a regular file for a symlink in between had the chmod land
# on a host file and the host file's bytes packed into the archive under an
# innocent name. Carrying (dir_fd, name) instead means every call names an
# inode the walk itself opened with O_NOFOLLOW.
#
# The three passes -- relax permissions, measure, archive -- each walk the
# tree with _walk_tree(), which visits an entry before descending into it.
# That ordering is what lets the first pass chmod a directory it is about
# to enter, so a chmod-000 subtree is now reachable at all: os.walk() gave
# up on one silently and left its contents out of the backup.

import os
import stat
import sys
import tarfile

from proot_distro import dirfd
from proot_distro.compress import (
    ZSTD_AVAILABLE, open_tar_writer, unavailable_msg, unsupported_msg,
)
from proot_distro.l2s import open_l2s_backing, resolve_l2s_target
from proot_distro.message import (
    log_info, log_error, crit_error, quote_error, quote_path,
)
from proot_distro.progress import (
    REDRAW_THRESHOLD_BYTES, clear_bar, draw_bytes_bar,
)
from proot_distro.locking import ContainerLock
from proot_distro.names import require_valid_name
from proot_distro.paths import (
    container_is_installed, container_manifest, container_rootfs,
    open_container_dir,
)


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
    ('.tar.zst',  'zst'),
    ('.tzst',     'zst'),
    ('.tar',      ''),
)

# Extensions that look like compression requests but are not supported.
_UNSUPPORTED_EXTS = ('.tar.lz4', '.tar.lz')

# Maps --compress argument values to tarfile compression identifiers.
_COMPRESSION_ARG_MAP = {
    'gzip':  'gz',
    'bzip2': 'bz2',
    'xz':    'xz',
    'zstd':  'zst',
    'none':  '',
}


def _compression_mode(filename: str) -> str:
    """Return the tarfile compression suffix for *filename*'s extension.

    Raises ValueError for recognised-but-unsupported formats — which
    includes zstd on an interpreter that cannot write it, since the
    extension is what asked for the format.
    Falls back to uncompressed ('') for unknown extensions.
    """
    low = filename.lower()
    for ext, comp in _COMPRESS_EXTS:
        if low.endswith(ext):
            if comp == 'zst' and not ZSTD_AVAILABLE:
                raise ValueError(unsupported_msg(f"output format '{ext}'"))
            return comp
    for ext in _UNSUPPORTED_EXTS:
        if low.endswith(ext):
            raise ValueError(f"compression format '{ext}' is not supported.")
    return ''


def _listing(dir_fd: int) -> list:
    """Sorted entry names of the directory dir_fd refers to; [] on failure."""
    try:
        return dirfd.listdir_at(dir_fd)
    except OSError:
        return []


def _walk_tree(parent_fd, name, arcname, path, visit, skip=()):
    """Visit *name* under parent_fd and everything below it, depth first.

    ``visit(dir_fd, entry, arcname, path, st)`` is called for the entry
    itself before anything it contains, so a directory member always
    precedes what it holds — which is what `restore` needs, and what lets
    the permission pass relax a directory just before this walk enters it.

    Every call is addressed as (directory fd, single name); *path* comes
    along only because resolve_l2s_target() has to know where a symlink sat
    to make sense of a relative target, and it is never opened by name —
    open_l2s_backing() re-walks it from a descriptor on the rootfs.

    Directories are carried on an explicit stack rather than by recursion:
    how deep the tree goes is the guest's choice, and one past the
    interpreter's limit would end the backup in a RecursionError. How many
    descriptors that costs is bounded too (dirfd.Levels): one per level
    ran the process out of them partway down a deep rootfs, and each
    directory the walk could then not open was skipped silently, so the
    archive came back missing everything below that depth and said so
    nowhere.

    *skip* names entries left out at the top level only — proot's `.l2s/`
    store, whose files are inlined into the symlinks that refer to them.
    """
    try:
        st = dirfd.lstat_at(parent_fd, name)
    except OSError:
        return
    visit(parent_fd, name, arcname, path, st)
    if not stat.S_ISDIR(st.st_mode):
        return
    try:
        fd = dirfd.opendir_at(parent_fd, name)
    except OSError:
        return

    pending = [n for n in _listing(fd) if n not in skip]
    pending.reverse()
    # Frame layout: [fd, None, arcname, path, pending, owned] — the two
    # descriptor slots first and `owned` last, so close_frames() unwinds it.
    stack = [[fd, None, arcname, path, pending, True]]
    levels = dirfd.Levels(stack)
    try:
        while stack:
            fd, _, arc_dir, dir_path, pending, _ = stack[-1]
            if not pending:
                levels.pop()
                os.close(fd)
                continue
            child = pending.pop()
            child_arc = os.path.join(arc_dir, child)
            child_path = os.path.join(dir_path, child)
            try:
                cst = dirfd.lstat_at(fd, child)
            except OSError:
                continue
            visit(fd, child, child_arc, child_path, cst)
            if not stat.S_ISDIR(cst.st_mode):
                continue
            try:
                sub = dirfd.opendir_at(fd, child)
            except OSError:
                continue
            names = _listing(sub)
            names.reverse()
            levels.push([sub, None, child_arc, child_path, names, True])
    except BaseException:
        dirfd.close_frames(stack)
        raise


def _each_entry(container_fd, container_name, rootfs_dir, manifest_path,
                visit):
    """Call *visit* for manifest.json, then for every entry of the rootfs.

    Both live directly under the container directory, and *container_fd*
    is the descriptor the whole command holds on it -- opened once, by
    the O_NOFOLLOW walk, and never resolved again. Opening
    `containers/<name>` here was the one step of this command still done
    by name, and it was done three times (relax, measure, archive): the
    directory is guest-writable on Termux and `backup` holds only a
    shared lock, so a live session could re-point it between the
    installed check and any of them and have the pass run against a host
    directory of its choosing.
    """
    manifest_name = os.path.basename(manifest_path)
    rootfs_name = os.path.basename(rootfs_dir)
    # manifest.json is optional (legacy containers have none) and is
    # only ever a plain file; anything else under that name is skipped
    # rather than followed.
    try:
        mst = dirfd.lstat_at(container_fd, manifest_name)
    except OSError:
        mst = None
    if mst is not None and stat.S_ISREG(mst.st_mode):
        visit(container_fd, manifest_name,
              os.path.join(container_name, manifest_name),
              manifest_path, mst)
    _walk_tree(container_fd, rootfs_name,
               os.path.join(container_name, rootfs_name), rootfs_dir,
               visit, skip=(".l2s",))


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


def _strip_owner(info: tarfile.TarInfo) -> None:
    """Zero the ownership fields: a restore must not depend on host ids."""
    info.uid = 0
    info.gid = 0
    info.uname = ''
    info.gname = ''


def _add_path(
    tf: tarfile.TarFile, dir_fd: int, name: str, arcname: str,
    path: str, st, rootfs: str, rootfs_fd: int, on_read=None,
) -> None:
    """Add *name* under dir_fd to *tf* as *arcname*, stripping ownership.

    Block/character devices, FIFOs, and sockets are silently skipped.
    Symlinks are stored as symlinks (not followed) unless they are
    proot link2symlink emulated hard links — i.e. symlinks whose target
    basename matches the link2symlink prefix (see resolve_l2s_target).
    Those are resolved to the backing file's content and packed as
    regular files so the archive is self-contained and survives being
    restored to a different path. Regular files and directories are
    stored with their permissions intact.

    *st* is the lstat the walk already took, and it decides the member's
    type; *path* is the entry's host path, used only to make sense of a
    relative l2s target. Every filesystem call goes through (dir_fd, name),
    so nothing here can be redirected by an entry swapped for a symlink
    since the walk saw it — which a live session sharing the container's
    shared lock is free to do.

    *rootfs* is the container's rootfs root, used to confine resolved
    l2s targets to the rootfs subtree; *rootfs_fd* is the descriptor the
    command pinned it as, which is what the backing file is then opened
    through — resolving the rootfs path again would undo the pin.

    *on_read*, when provided, is called with the byte count of each chunk
    read from a regular file so callers can track progress during compression.
    """
    m = st.st_mode
    if (stat.S_ISBLK(m) or stat.S_ISCHR(m)
            or stat.S_ISFIFO(m) or stat.S_ISSOCK(m)):
        return

    if stat.S_ISLNK(m):
        try:
            target = os.readlink(name, dir_fd=dir_fd)
        except OSError:
            return
        # Detect proot link2symlink symlinks (regardless of whether their
        # intermediate is stashed in <rootfs>/.l2s/ or alongside the
        # original) and pack their backing files' content as regular
        # files. Multiple l2s symlinks sharing one backing file become
        # independent regular files in the archive — the guest-side
        # hard-link semantics are lost, file content is preserved, and
        # the archive carries no absolute paths into the source rootfs
        # that would dangle after restore.
        l2s_path = resolve_l2s_target(path, target, rootfs)
        if l2s_path is not None:
            # Through a descriptor, not the name: the resolve and the
            # read are two steps and backup holds only a shared lock,
            # so a live session could re-point a component in between
            # (see l2s.open_l2s_backing).
            opened = open_l2s_backing(rootfs, l2s_path,
                                      rootfs_fd=rootfs_fd)
            if opened is not None:
                cfd, cst = opened
                try:
                    info = tarfile.TarInfo(arcname)
                    info.type = tarfile.REGTYPE
                    info.size = cst.st_size
                    info.mode = stat.S_IMODE(cst.st_mode)
                    info.mtime = int(cst.st_mtime)
                    _strip_owner(info)
                    try:
                        fh = open(cfd, 'rb', closefd=False)
                        tf.addfile(
                            info,
                            _ReadCounter(fh, on_read) if on_read else fh,
                        )
                    except OSError:
                        pass
                finally:
                    os.close(cfd)
                return
            # Backing file missing, unreadable or non-regular: fall
            # through and store the symlink as-is.
        info = tarfile.TarInfo(arcname)
        info.type = tarfile.SYMTYPE
        info.linkname = target
        info.size = 0
        info.mode = stat.S_IMODE(m)
        info.mtime = int(st.st_mtime)
        _strip_owner(info)
        tf.addfile(info)
        return

    if stat.S_ISDIR(m):
        info = tarfile.TarInfo(arcname)
        info.type = tarfile.DIRTYPE
        info.size = 0
        info.mode = stat.S_IMODE(m)
        info.mtime = int(st.st_mtime)
        _strip_owner(info)
        tf.addfile(info)
        return

    if not stat.S_ISREG(m):
        return

    try:
        fd, _fst = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    except OSError:
        return
    try:
        fh = open(fd, 'rb', closefd=False)
        # gettarinfo off the open descriptor rather than the name. It is
        # what keeps tarfile's (dev, ino) table, so a second name for a
        # file already in the archive becomes a hard-link member instead
        # of a second copy of the content; and taking the size from the
        # fd we are about to read means the header describes the very
        # inode whose bytes follow it.
        info = tf.gettarinfo(arcname=arcname, fileobj=fh)
        _strip_owner(info)
        tf.addfile(info, _ReadCounter(fh, on_read) if on_read else fh)
    except OSError:
        pass
    finally:
        os.close(fd)


def _relax_permissions(dir_fd, name, _arcname, _path, st) -> None:
    """Make one entry readable by its owner, best effort.

    Called for every entry *before* the walk descends into it, so a
    directory the backup could not otherwise enter is opened on the next
    step. os.walk() could not do that: it lists a directory before handing
    it over, so a chmod-000 one was skipped outright and its whole subtree
    stayed out of the archive.

    The chmod goes through a descriptor (dirfd.chmod_at): naming the entry
    would hand the mode change to whatever a symlink planted since the
    lstat points at, which is a host file with bits of the guest's
    choosing.
    """
    m = st.st_mode
    if stat.S_ISDIR(m):
        needed = stat.S_IRUSR | stat.S_IXUSR
    elif stat.S_ISREG(m):
        needed = stat.S_IRUSR
        if m & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH):
            needed |= stat.S_IXUSR
    else:
        return
    mode = stat.S_IMODE(m)
    if mode | needed != mode:
        dirfd.chmod_at(dir_fd, name, mode | needed)


def _fix_permissions(container_fd: int, rootfs_dir: str) -> None:
    """Ensure all dirs and files in *rootfs_dir* are readable by owner.

    Named off the container directory's own descriptor, like the two
    passes that follow it: this one *writes* -- it chmods what it walks
    -- so resolving `containers/<name>` again would hand those mode
    changes to whatever the name led to by then.
    """
    _walk_tree(container_fd, os.path.basename(rootfs_dir), '', rootfs_dir,
               _relax_permissions)


def command_backup(args) -> None:
    """Archive an installed container to a tar file or stdout."""
    container_name = args.container_name
    output_path = getattr(args, "output", None)
    compression_arg = getattr(args, "compression", None)
    verbose = getattr(args, "verbose", False)

    require_valid_name(container_name)

    rootfs_dir = container_rootfs(container_name)
    manifest_path = container_manifest(container_name)

    if not container_is_installed(container_name):
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
                crit_error(str(exc))
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

    # `--compress zstd` stays a valid choice on every interpreter so the
    # refusal can say why instead of argparse's bare "invalid choice".
    if compression == 'zst' and not ZSTD_AVAILABLE:
        crit_error(unavailable_msg("compression type 'zstd'"))
        sys.exit(1)

    with ContainerLock(container_name, exclusive=False, command="backup"):
        # One descriptor on containers/<name> for the whole run, taken
        # by the same O_NOFOLLOW walk the installed check used, and one
        # on the rootfs below it. Everything after this is named as
        # (dir_fd, entry): the three passes used to reopen
        # containers/<name> by path, and `backup` holds only a shared
        # lock on purpose, so a live session was free to re-point it in
        # between and have the permission pass chmod -- and the archiver
        # pack -- a host directory of its choosing.
        try:
            container_fd = open_container_dir(container_name)
        except FileNotFoundError:
            crit_error(f"container '{container_name}' does not exist.")
            sys.exit(1)
        try:
            try:
                rootfs_fd = dirfd.opendir_at(
                    container_fd, os.path.basename(rootfs_dir),
                )
            except OSError as exc:
                crit_error(f"cannot read the rootfs of container "
                           f"'{container_name}': {quote_error(exc)}")
                sys.exit(1)
            try:
                _run_backup(
                    container_fd, rootfs_fd, container_name, rootfs_dir,
                    manifest_path, output_path, compression, verbose,
                )
            finally:
                os.close(rootfs_fd)
        finally:
            os.close(container_fd)


def _run_backup(
    container_fd, rootfs_fd, container_name, rootfs_dir, manifest_path,
    output_path, compression, verbose,
):
    log_info(f"Backing up '{container_name}'...")

    if output_path:
        log_info(f"Will write backup data to '{output_path}'.")
    else:
        log_info("Will write backup data to stdout.")

    log_info("Fixing file permissions in rootfs...")
    _fix_permissions(container_fd, rootfs_dir)

    # Pre-compute total size of payload bytes to drive the progress bar.
    # Regular files contribute their own size; l2s symlinks contribute
    # the size of their backing file (since _add_path will inline that
    # content in place of the symlink). The archive prefix is just the
    # container name (e.g. "ubuntu/"); `.l2s` is left out at the rootfs
    # root because its files are inlined into their referring symlinks.
    total_size = 0

    def _measure(dir_fd, name, _arc, path, st) -> None:
        nonlocal total_size
        if stat.S_ISREG(st.st_mode):
            total_size += st.st_size
            return
        if not stat.S_ISLNK(st.st_mode):
            return
        try:
            target = os.readlink(name, dir_fd=dir_fd)
        except OSError:
            return
        l2s_path = resolve_l2s_target(path, target, rootfs_dir)
        if l2s_path is None:
            return
        opened = open_l2s_backing(rootfs_dir, l2s_path,
                                  rootfs_fd=rootfs_fd)
        if opened is None:
            return
        cfd, cst = opened
        os.close(cfd)
        total_size += cst.st_size

    _each_entry(container_fd, container_name, rootfs_dir, manifest_path,
                _measure)

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
            # The arcname carries the rootfs entry's own name, which the
            # guest chose; an ESC in one repaints the terminal.
            log_info(f"Adding: '{quote_path(arc)}'")
        _draw_bar()

    def _open_archive():
        """Return the context manager producing the TarFile to write."""
        if compression == 'zst':
            # Not tarfile's own zstd mode: `w|zst` refuses a compression
            # level, so a piped backup would be stuck at libzstd's
            # default while `-o file.tar.zst` could pick one. See
            # proot_distro.compress.
            return open_tar_writer(
                output_path,
                None if output_path else sys.stdout.buffer,
            )
        tar_mode = f'w:{compression}' if output_path else f'w|{compression}'
        return tarfile.open(
            output_path if output_path else None,
            fileobj=None if output_path else sys.stdout.buffer,
            mode=tar_mode,
        )

    try:
        with _open_archive() as tf:
            def _archive(dir_fd, name, arc, path, st) -> None:
                _add_path(tf, dir_fd, name, arc, path, st, rootfs_dir,
                          rootfs_fd, on_read=_on_read)
                _on_entry(arc)

            _each_entry(container_fd, container_name, rootfs_dir,
                        manifest_path, _archive)

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
        log_error(f"Failed to create backup archive: {quote_error(exc)}")
        if output_path:
            try:
                os.remove(output_path)
            except OSError:
                pass
        sys.exit(1)
