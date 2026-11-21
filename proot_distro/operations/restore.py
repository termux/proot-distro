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

# Architecture: Extracts a proot container backup from a TAR archive.
# Expected archive structure: <name>/manifest.json + <name>/rootfs/*.
# Exactly one container is restored per archive (all `backup` ever
# writes); a member naming a second container is rejected.
# Legacy archives with installed-rootfs/<name> layout are also accepted:
# contents are re-rooted to containers/<name>/rootfs/. Archives with no
# subdirectory, and archives that do not produce a rootfs directory, are
# rejected: the clear is deferred to the first rootfs member that actually
# materialises and the manifest is written only on success, so a rejected
# archive leaves the target untouched (or, if a broken rootfs was written,
# the partial result is removed rather than left rootfs-less). Compression
# is auto-detected via tarfile r|* (archive file) or from header magic
# bytes (stdin) — zstd included, where the interpreter can read it; where
# it cannot, the archive is named as zstd rather than reported as a
# corrupt tar (see proot_distro.compress). For file input,
# progress is tracked in compressed bytes consumed so total_size is
# os.path.getsize() — instant, no upfront scan needed.
#
# Nothing is written by path. The directory the container is built in is
# opened once, at the commit point, made by container_staging.py under the
# descriptor of an O_NOFOLLOW walk to CONTAINERS_DIR, and every member is
# written as (dir_fd, name) below it. Two different things made that necessary. The
# container directory itself is guest-writable on Termux, where the
# runtime tree sits under the $TERMUX_PREFIX bound read-write into every
# non-isolated container, so a planted `containers/<name> -> <host dir>`
# had the whole archive extracted inside that host directory — _safe_dest
# clamped every member under the container directory, which is exactly
# where the link led, and the check that the rootfs was not a symlink ran
# after the writes. And a member's own parents are archive content: the
# name is resolved through them with tar_extract.safe_resolve_parts,
# which follows a symlink an earlier member shipped but re-anchors it at
# the rootfs, and the resolved components are then re-walked with
# O_NOFOLLOW, so a name that was validated is the name that is written.
# os.makedirs/os.symlink/shutil.copy2/open(dest, 'wb') each resolved the
# whole path afresh, which is one resolution too many.
#
# The container is built in a staging directory instead, renamed onto
# containers/<name> only once it is complete (container_staging.py), so a
# restore killed part-way leaves no container behind. A restore over an
# existing container removes the old one completely first
# (clear_container), so the two never take up space at the same time;
# the archive is still there to restore from if this one fails.

import errno
import os
import stat
import tarfile

from proot_distro import dirfd
from proot_distro.atomic import atomic_write_at
from proot_distro.compress import (
    ZSTD_AVAILABLE, ZSTD_MAGIC, file_is_zstd, header_is_zstd, unsupported_msg,
)
from proot_distro.constants import PROGRAM_NAME
from proot_distro.container_staging import (
    clear_container, create_staging, reclaim_staging, refuse_leftovers,
)
from proot_distro.interrupts import open_tar_member
from proot_distro.message import quote_error, quote_path
from proot_distro.operation_errors import (
    BusyFailure, IntegrityFailure, NotFoundFailure, OperationFailure,
    UnsupportedFailure, attach_recovery,
)
from proot_distro.operations.context import OperationContext
from proot_distro.operations.models import ArchiveRestore
from proot_distro.progress import (
    ByteCounter, clear_bar, draw_bytes_bar,
)
from proot_distro.locking import ContainerLock
from proot_distro.names import is_valid_name
from proot_distro.paths import container_entry_lstat, container_is_installed
from proot_distro.helpers.tar_extract import safe_resolve_parts


# Magic-byte signatures used to identify compressed streams.
_MAGIC_COMPRESS = (
    (b'\x1f\x8b',      'gz'),   # gzip
    (b'BZh',           'bz2'),  # bzip2
    (b'\xfd7zXZ\x00',  'xz'),   # xz
    (b'\x5d\x00',      'xz'),   # lzma legacy (lzma.open handles both)
    (ZSTD_MAGIC,       'zst'),  # zstandard (Python 3.14+)
)

# Legacy archive prefix.
_LEGACY_PREFIX = "installed-rootfs"

# How much of a `manifest.json` member is worth holding in memory. The
# archive is a stranger's file -- someone hands the user a backup and
# `restore` is what opens it -- so its member sizes are its own choice,
# and this one member is read whole (it is written only once the rootfs
# is committed, so it cannot be streamed to the destination). A real one
# is a few kilobytes of image metadata; 16 MiB is orders of magnitude
# above that and still bounded, the same ceiling install_local puts on
# the JSON inside an OCI archive.
_MAX_MANIFEST_BYTES = 16 * 1024 * 1024


def _detect_compression(header: bytes) -> str:
    """Return the tarfile mode suffix inferred from *header* magic bytes."""
    for magic, mode in _MAGIC_COMPRESS:
        if header.startswith(magic):
            return mode
    return ''


class _ArchiveReadError(OSError):
    """An OSError reading the archive, as opposed to writing the container.

    The two used to share one handler, which called every OSError a sign
    of a corrupted archive and raised IntegrityFailure: a full disk while
    the container was written said the backup was bad. Now that a restore
    over a container removes the old one first (container_staging.
    clear_container), that message could have the user throw away the
    one good copy left.
    """


class _ArchiveSource:
    """The archive's bytes, with a failure to read them told apart.

    Every read of the archive goes through here, and an OSError it raises
    comes out as _ArchiveReadError. Whatever else raises OSError during a
    restore is writing the container.
    """

    def __init__(self, raw) -> None:
        self._raw = raw

    def read(self, size=-1):
        try:
            return self._raw.read(size)
        except OSError as exc:
            raise _ArchiveReadError(exc.errno, exc.strerror or str(exc)) \
                from exc

    def readinto(self, buffer):
        try:
            return self._raw.readinto(buffer)
        except OSError as exc:
            raise _ArchiveReadError(exc.errno, exc.strerror or str(exc)) \
                from exc

    def __getattr__(self, name):
        return getattr(self._raw, name)


# What a failed write into the container says beyond its reason, for the
# reasons a restore can do something about.
_WRITE_HINTS = {
    errno.ENOSPC: "Free up some space and restore the archive again.",
    errno.EDQUOT: "Free up some space and restore the archive again.",
}


class _PrefixedReader:
    """Replay bytes consumed for format detection before reading a stream."""

    def __init__(self, prefix, stream):
        self._prefix = bytearray(prefix)
        self._stream = stream

    def read(self, size=-1):
        if size is None or size < 0:
            prefix = bytes(self._prefix)
            self._prefix.clear()
            return prefix + self._stream.read()
        if not size:
            return b""
        prefix = bytes(self._prefix[:size])
        del self._prefix[:len(prefix)]
        if len(prefix) == size:
            return prefix
        return prefix + self._stream.read(size - len(prefix))


def _ignore(_rel, _exc) -> None:
    """Swallow a removal failure, the way dirfd.remove_tree() does."""


def _remove_existing(dir_fd: int, name: str, member: tarfile.TarInfo) -> None:
    """Remove any existing entry at (dir_fd, name) before extraction.

    The directory branch is reached when the archive puts a non-directory
    where the container already holds a tree, so what is being discarded
    is the *previous* content — as deep and as sealed as it was left.
    shutil.rmtree() recursed, and RecursionError is not an OSError, so the
    handler here would not have caught it. Everything else is unlinked by
    name under the descriptor, which is also how a planted symlink goes:
    as the link, never as what it points at.
    """
    try:
        st = dirfd.lstat_at(dir_fd, name)
    except OSError:
        return
    if not stat.S_ISDIR(st.st_mode):
        dirfd.unlink_quietly(dir_fd, name)
    elif not member.isdir():
        dirfd.rmtree_at(dir_fd, name, force=True, on_error=_ignore)


_SKIP = (None, None)


def _dest_path(member_name: str) -> tuple:
    """Map a TAR member name to (container_name, parts under its directory).

    *parts* are the components below `containers/<name>`, which is what
    the extraction descends from — ('rootfs', 'etc', 'hostname'),
    ('manifest.json',), or () for the container directory itself.
    Returns (None, None) if the member should be skipped. Supported
    archive layouts:

      1. New format:    <name>/manifest.json or <name>/rootfs/...
      2. Legacy format: installed-rootfs/<name>/...
      3. No subdir or bare ./: rejected.

    Members containing '..' or absolute path components are rejected so
    a crafted archive cannot escape the containers directory. The name
    itself is checked against the shared name regex.
    """
    name = member_name.lstrip("/")
    if not name or name == ".":
        return _SKIP

    parts = name.split("/")

    # Reject '..' / '.' / empty components — blocks path traversal.
    if any(p in ("..", ".", "") for p in parts):
        return _SKIP

    # Archive starts at root with no real subdirectory — reject.
    if len(parts) == 1 and not name.endswith("/"):
        return _SKIP

    # Legacy format: installed-rootfs/<name>/...  ->  containers/<name>/rootfs/...
    if parts[0] == _LEGACY_PREFIX:
        if len(parts) < 2:
            return _SKIP
        container_name = parts[1]
        if not is_valid_name(container_name):
            return _SKIP
        return (container_name, ("rootfs",) + tuple(parts[2:]))

    # New format: <name>/...
    container_name = parts[0]
    if not is_valid_name(container_name):
        return _SKIP

    if len(parts) == 1:
        return (container_name, ())

    sub = parts[1]
    rest = parts[2:]

    if sub == "manifest.json" and not rest:
        return (container_name, ("manifest.json",))

    if sub == "rootfs":
        return (container_name, ("rootfs",) + tuple(rest))

    # <name>/<anything_else>  -> treated as a path inside rootfs for
    # back-compat with archives created by very old versions.
    return (container_name, ("rootfs",) + tuple(parts[1:]))


def _is_rootfs_dest(parts: tuple) -> bool:
    """Return True if *parts* names the rootfs directory or something in it.

    Distinguishes a real filesystem member — which commits the restore —
    from the only other thing a backup carries at the top level, the
    `manifest.json` sentinel. Covers the new `<name>/rootfs/...`, the
    legacy `installed-rootfs/<name>/...`, and the very-old `<name>/<other>`
    back-compat layouts, since `_dest_path` maps all of them under
    `rootfs`.
    """
    return bool(parts) and parts[0] == "rootfs"


class _Destinations:
    """Where a member's parts land, as (directory fd, entry name).

    Two steps, and both are needed. safe_resolve_parts() follows the
    symlink components an earlier member of the same archive shipped —
    that is what a rootfs looks like, `/var/run -> /run` and the rest —
    but re-anchors an absolute target at the rootfs and clamps '..'
    there, the same view proot gives the guest, so the answer always
    names something inside the container. That answer is then re-walked
    with O_NOFOLLOW from the descriptor on the container directory
    (dirfd.descend_at), because resolving by name and writing by name are
    two acts on two possibly different trees: the member after the
    resolve can be the one that plants the link. The final component is
    deliberately left unresolved, so the entry itself is written rather
    than whatever a same-named symlink leads to.

    The rootfs is the anchor, not the container directory: with the
    container directory standing in for '/', a `lib -> /usr/lib` an
    archive shipped resolved to `containers/<name>/usr/lib` and members
    under it were written beside the rootfs rather than inside it.

    One descriptor is cached, since a backup's members arrive in walk
    order and consecutive entries nearly always share a parent.
    """

    def __init__(self, root_fd: int, rootfs_path: str) -> None:
        self._root_fd = root_fd
        self._rootfs_path = rootfs_path
        self._key = None
        self._fd = None

    @property
    def key(self) -> tuple:
        """The resolved components the cached parent descriptor names."""
        return self._key

    def _split(self, parts: tuple, deref_leaf: bool):
        """(components of the parent, leaf name), or None.

        Everything below `rootfs` is resolved against the rootfs; the
        rootfs entry itself and `manifest.json` are direct children of
        the container directory and name no path to walk.
        """
        if len(parts) == 1 or parts[0] != "rootfs":
            return (), parts[-1]
        inner = list(parts[1:]) if deref_leaf else list(parts[1:-1])
        resolved = safe_resolve_parts(self._rootfs_path, inner)
        if resolved is None:
            return None
        if deref_leaf:
            if not resolved:        # resolves to the rootfs itself
                return None
            return ("rootfs",) + tuple(resolved[:-1]), resolved[-1]
        return ("rootfs",) + tuple(resolved), parts[-1]

    def parent_of(self, parts: tuple):
        """(dir_fd, leaf) for the entry *parts* names, or (None, None)."""
        split = self._split(parts, deref_leaf=False)
        if split is None:
            return None, None
        key, leaf = split
        if key != self._key:
            try:
                fd = dirfd.descend_at(self._root_fd, key, create=True)
            except OSError:
                return None, None
            self.close()
            self._key, self._fd = key, fd
        return self._fd, leaf

    def open_source(self, parts: tuple):
        """Open a hardlink's source inside the container. (fd, stat) or None.

        The whole path is resolved here, final component included: a hard
        link names a file, not an entry to replace. The read still goes
        through open_regular_at(), so a FIFO planted under the name cannot
        block the restore waiting for a peer that never comes.
        """
        split = self._split(parts, deref_leaf=True)
        if split is None:
            return None
        key, name = split
        try:
            fd = dirfd.descend_at(self._root_fd, key)
        except OSError:
            return None
        try:
            return dirfd.open_regular_at(fd, name, os.O_RDONLY)
        except OSError:
            return None
        finally:
            os.close(fd)

    def chmod(self, key: tuple, name: str, mode: int) -> None:
        """Apply *mode* to an already-written directory, best effort."""
        try:
            fd = dirfd.descend_at(self._root_fd, key)
        except OSError:
            return
        try:
            dirfd.chmod_at(fd, name, mode, only_dir=True)
        finally:
            os.close(fd)

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._key, self._fd = None, None


def _write_directory(dir_fd: int, name: str, member) -> tuple:
    """Create a directory member. Returns (mode, deferred) for the caller.

    A mode without owner rwx is applied only once the extraction is over —
    the archive's own children still have to be written inside it — so the
    caller keeps the pair and replays it at the end.
    """
    try:
        os.mkdir(name, 0o700, dir_fd=dir_fd)
    except FileExistsError:
        pass
    mode = stat.S_IMODE(member.mode)
    if (mode & stat.S_IRWXU) != stat.S_IRWXU:
        dirfd.chmod_at(dir_fd, name, mode | stat.S_IRWXU, only_dir=True)
        return mode, True
    dirfd.chmod_at(dir_fd, name, mode, only_dir=True)
    return mode, False


def _write_regular(dir_fd: int, name: str, member, fobj) -> None:
    """Write a regular member as a brand-new inode under dir_fd.

    open_new_at() is O_EXCL, so the bytes never go through a hardlink to
    somewhere else — the one thing O_NOFOLLOW cannot refuse, since a
    second name for a host file is that file, not a link to it. The mode
    goes on through the descriptor, since the one open() creates with is
    umask-masked.
    """
    fd, _st = dirfd.open_new_at(dir_fd, name, 0o600)
    try:
        with open(fd, "wb", closefd=False) as out:
            while True:
                chunk = fobj.read(1 << 17)   # 128 KiB
                if not chunk:
                    break
                out.write(chunk)
        try:
            os.fchmod(fd, stat.S_IMODE(member.mode))
        except OSError:
            pass
    finally:
        os.close(fd)


def _write_hardlink(dir_fd: int, name: str, member, source) -> None:
    """Materialise a hard-link member as a copy of *source*.

    Containers use proot's --link2symlink, so hard links on the host
    filesystem would share inodes across what the guest treats as
    independent files; the backup format stores the second name as its
    own member and the restore copies the content.
    """
    src_fd, src_st = source
    try:
        fd, _st = dirfd.open_new_at(dir_fd, name, 0o600)
    except OSError:
        return
    try:
        dirfd.copy_data(src_fd, fd, src_st)
        mode = stat.S_IMODE(member.mode) if member.mode else \
            stat.S_IMODE(src_st.st_mode)
        try:
            os.fchmod(fd, mode)
        except OSError:
            pass
    finally:
        os.close(fd)


def restore_container(
    *, archive_path=None, archive_file=None, context=None,
) -> ArchiveRestore:
    """Reinstate a single container from a tar backup (see _restore).

    A restore over a container removes the old one before it writes the
    new (container_staging.clear_container). If it then fails, the user
    is told to restore the archive again, and so is an API caller that
    reads no events (attach_recovery).
    """
    context = context if context is not None else OperationContext()
    outcome = _Outcome()
    try:
        return _restore(archive_path, archive_file, context, outcome)
    except BaseException as exc:
        if outcome.cleared and container_entry_lstat(outcome.name) is None:
            note = (
                f"Container '{outcome.name}' was removed, but the restore "
                f"did not finish. Restore the archive again to bring it "
                f"back."
            )
            context.error(note, phase="extract", resource=outcome.name)
            attach_recovery(exc, note)
        raise


class _Outcome:
    """What _restore() did that its caller has to know if it fails."""

    def __init__(self) -> None:
        self.name = None        # the container restored
        self.cleared = False    # an existing one was removed for it


def _restore(archive_path, archive_file, context, outcome) -> ArchiveRestore:
    """Reinstate a single container from a tar backup.

    An archive is expected to hold exactly one container (this is all
    `backup` ever produces). The first valid member fixes the target;
    any member naming a different container makes the restore ambiguous
    and is rejected, so a hand-crafted or legacy multi-container archive
    can never silently overwrite more than the user asked for.

    The archive must produce a rootfs directory. The destructive clear is
    deferred until the first rootfs member that actually materialises, and
    the manifest is written only once the rootfs is confirmed. A rootfs-less
    archive — a manifest-only or empty backup, or the wrong file entirely —
    therefore leaves the target untouched and is rejected; an archive that
    writes a broken (non-directory) rootfs has that partial result removed
    so no rootfs-less container is left behind.

    The new container is built in a staging directory and renamed onto its
    name at the end (container_staging.py); an existing one is removed
    completely at the commit point, before the first member is written.

    Returns immutable metadata identifying the restored container and input.
    """
    context = context if context is not None else OperationContext()
    archive = archive_path

    if archive:
        if not os.path.exists(archive):
            raise NotFoundFailure(f"file '{archive}' does not exist.")
        if os.path.isdir(archive):
            raise OperationFailure(f"path '{archive}' is a directory.")
        if not os.access(archive, os.R_OK):
            raise OperationFailure(f"file '{archive}' is not readable.")
    elif archive_file is None:
        raise OperationFailure("restore archive input is not specified.")

    context.info(
        "Restoring container from the backup...",
        phase="extract", resource=archive,
    )

    done_size = 0
    total_size = 0
    counter = None

    def _on_entry(member_size: int, member_name: str) -> None:
        nonlocal done_size
        done_size += member_size
        if context.verbose:
            # Straight off the archive, which is whatever the user was
            # handed: a member named with ESC repaints the terminal.
            context.info(
                f"Extracting: '{quote_path(member_name)}'",
                phase="extract", resource=archive,
            )
        if counter is not None and total_size:
            draw_bytes_bar(
                counter.count, total_size, phase="extract",
                resource=archive,
            )
        else:
            draw_bytes_bar(
                done_size, 0, noun="extracted", phase="extract",
                resource=archive,
            )

    def _check_bare_root(member_name: str) -> bool:
        """Return True if this member has no real subdirectory (reject)."""
        name = member_name.lstrip('/')
        if not name:
            return False
        parts = name.split('/')
        return len(parts) == 1 and not name.endswith('/')

    stream_header = b""
    stream_input = archive_file
    if not archive:
        source = _ArchiveSource(archive_file)
        try:
            stream_header = source.read(6)
        except _ArchiveReadError as exc:
            raise OperationFailure(
                f"cannot read the archive input stream: {quote_error(exc)}"
            ) from exc
        if not isinstance(stream_header, bytes):
            raise OperationFailure("archive stream did not return bytes")
        stream_input = _PrefixedReader(stream_header, source)

    # A zstd archive on an interpreter that cannot read one would reach
    # tarfile as an unrecognisable stream and be reported as corruption,
    # so it is named here instead — before anything is opened.
    if not ZSTD_AVAILABLE:
        if archive:
            unsupported, subject = file_is_zstd(archive), f"archive '{archive}'"
        else:
            unsupported = header_is_zstd(stream_header)
            subject = "the archive input stream"
        if unsupported:
            raise UnsupportedFailure(unsupported_msg(subject))

    raw_fh = None
    # Restore targets exactly one container and only mutates it once the
    # archive proves it carries real filesystem content. The first valid
    # member fixes the name and acquires the exclusive lock (non-destructive).
    # The destructive clear is deferred to the first rootfs member that is
    # actually materialised, and the manifest is buffered and written only on
    # success — so an archive that yields no rootfs leaves the target
    # untouched, and one that yields a broken rootfs is removed rather than
    # left rootfs-less. A member naming a different container is rejected.
    restore_name = None
    lock = None
    committed = False
    staging = None              # where the container is built, at commit
    succeeded = False
    interrupted = False
    dests = None                # opened with the container dir, at commit
    root_fd = None
    pending_manifest = None     # (bytes, mode) written only on success
    # Dirs whose archived mode lacks owner rwx: temporarily widened so we
    # can write into them, with the final chmod deferred until extraction
    # finishes. Applied in reverse insertion order so children are sealed
    # before their parents.
    deferred_dir_modes: list = []

    def _write_manifest(data: bytes, mode: int) -> None:
        # Published through atomic_write_at, in the container directory
        # this command holds open (or the staging directory that becomes
        # it), renaming the temporary onto the name under that very
        # descriptor. open(path, 'wb') truncated whatever the name led to.
        try:
            with atomic_write_at(root_fd, "manifest.json") as fh:
                fh.write(data)
                fh.flush()
                try:
                    os.fchmod(fh.fileno(), mode)
                except OSError:
                    pass
        except OSError:
            return

    try:
        if archive:
            try:
                total_size = os.path.getsize(archive)
                raw_fh = open(archive, 'rb')
            except OSError as exc:
                raise _ArchiveReadError(
                    exc.errno, exc.strerror or str(exc)
                ) from exc
            counter = ByteCounter(_ArchiveSource(raw_fh))
            tf_kwargs = dict(fileobj=counter, mode='r|*')
        else:
            comp = _detect_compression(stream_header)
            tf_kwargs = dict(fileobj=stream_input, mode=f'r|{comp}')

        with tarfile.open(**tf_kwargs) as tf:
            for member in tf:
                if member.isblk() or member.ischr() or member.isfifo():
                    continue

                if _check_bare_root(member.name):
                    clear_bar()
                    context.error(
                        f"Cannot restore: provided file has invalid "
                        f"structure. Only archives created by "
                        f"'{PROGRAM_NAME} backup' are supported.",
                        phase="extract", resource=archive,
                    )
                    raise IntegrityFailure(
                        "backup archive has invalid structure"
                    )

                container_name, parts = _dest_path(member.name)
                if container_name is None:
                    continue

                # Only one container may be restored per archive. The first
                # valid member fixes the target and acquires its exclusive
                # lock; a member naming a different container is rejected so a
                # multi-container archive can't overwrite more than the user
                # asked for. Archives are streamed, so this is detected on the
                # fly rather than by pre-scanning the member names.
                if restore_name is None:
                    restore_name = container_name
                    lock = ContainerLock(
                        container_name, exclusive=True, command="restore"
                    )
                    if not lock.acquire():
                        clear_bar()
                        detail = lock.blocked_detail()
                        if detail:
                            message = (f"Cannot restore: cannot lock "
                                       f"container '{container_name}': "
                                       f"{detail}")
                        else:
                            message = (f"Cannot restore: container "
                                       f"'{container_name}' is busy"
                                       f"{lock.holder_hint()}.")
                        context.error(
                            message, phase="extract", resource=restore_name
                        )
                        raise BusyFailure(message)
                    context.info(
                        f"Destination: {restore_name}",
                        phase="extract", resource=restore_name,
                    )
                elif container_name != restore_name:
                    clear_bar()
                    context.error(
                        f"Cannot restore: archive contains more than one "
                        f"container ('{restore_name}' and '{container_name}'). "
                        f"Restore handles a single container at a time.",
                        phase="extract", resource=archive,
                    )
                    raise IntegrityFailure(
                        "backup archive contains more than one container"
                    )

                # Non-rootfs members (only manifest.json in a real backup)
                # are held back: the manifest is buffered and written only if
                # the restore succeeds, so a rootfs-less archive never
                # clobbers the target's metadata. Anything else is ignored.
                if not _is_rootfs_dest(parts):
                    if member.isreg() and parts == ("manifest.json",):
                        fobj = open_tar_member(tf, member)
                        data = b''
                        if fobj is not None:
                            try:
                                # Capped on the bytes actually drawn, not
                                # on member.size: the header is the
                                # archive's to declare either way, so one
                                # more byte than the limit is read and its
                                # presence is the refusal.
                                data = fobj.read(_MAX_MANIFEST_BYTES + 1)
                            finally:
                                fobj.close()
                        if len(data) > _MAX_MANIFEST_BYTES:
                            clear_bar()
                            context.error(
                                f"Cannot restore: the archive's "
                                f"'manifest.json' is larger than "
                                f"{_MAX_MANIFEST_BYTES} bytes. Only "
                                f"archives created by '{PROGRAM_NAME} "
                                f"backup' are supported.",
                                phase="extract", resource=archive,
                            )
                            raise IntegrityFailure(
                                "backup manifest exceeds its size limit"
                            )
                        pending_manifest = (data, stat.S_IMODE(member.mode))
                        _on_entry(member.size, member.name)
                    continue

                # Resolve a hardlink's source, and skip members that will not
                # materialise (dangling hardlink, unknown type) *before*
                # clearing anything — so an archive whose only rootfs entries
                # don't resolve never destroys the existing rootfs. Only the
                # *reading* of that source waits for the commit, since it
                # goes through the descriptor opened there.
                link_parts = None
                if member.islnk():
                    link_container, link_parts = _dest_path(member.linkname)
                    if link_parts is None or link_container != restore_name:
                        # Linkname resolves nowhere or points at a different
                        # container — must not read out of an unrelated
                        # rootfs.
                        continue
                elif not (member.isdir() or member.issym() or member.isreg()):
                    continue

                # The member will produce rootfs content: remove the old
                # container, if there is one, and start the new one, once,
                # now. This is the destructive commit point, reached only
                # for a member that actually materialises something.
                if not committed:
                    # The container directory is opened here rather than
                    # earlier for the same reason the clear is deferred: an
                    # archive that never gets this far must leave no trace.
                    # The exclusive lock is held, so no other command can
                    # create the name between this test and what follows.
                    reclaim_staging(lambda count: context.info(
                        f"Removing what {count} interrupted install(s) or "
                        f"restore(s) left...",
                        phase="extract", resource=restore_name,
                    ))
                    if container_is_installed(restore_name,
                                              raise_errors=True):
                        # Gone completely before the new one is written,
                        # so the two never take up space at once.
                        context.info(
                            f"Removing the old container "
                            f"'{restore_name}'...",
                            phase="extract", resource=restore_name,
                        )
                        outcome.name = restore_name
                        outcome.cleared = True
                        clear_container(restore_name, context,
                                        phase="extract",
                                        resource=restore_name)
                    else:
                        # Leftovers under the name are refused with the
                        # command that clears them.
                        refuse_leftovers(restore_name)
                    staging = create_staging()
                    root_fd = staging.fd
                    dests = _Destinations(root_fd, staging.rootfs_path)
                    committed = True

                dir_fd, leaf = dests.parent_of(parts)
                if dir_fd is None:
                    continue

                if member.islnk():
                    source = dests.open_source(link_parts)
                    if source is None:
                        continue
                    try:
                        _remove_existing(dir_fd, leaf, member)
                        _write_hardlink(dir_fd, leaf, member, source)
                    finally:
                        os.close(source[0])

                elif member.isdir():
                    _remove_existing(dir_fd, leaf, member)
                    mode, deferred = _write_directory(dir_fd, leaf, member)
                    if deferred:
                        deferred_dir_modes.append((dests.key, leaf, mode))

                elif member.issym():
                    _remove_existing(dir_fd, leaf, member)
                    os.symlink(member.linkname, leaf, dir_fd=dir_fd)

                else:                       # member.isreg()
                    fobj = open_tar_member(tf, member)
                    if fobj is None:
                        continue
                    try:
                        _remove_existing(dir_fd, leaf, member)
                        _write_regular(dir_fd, leaf, member, fobj)
                    finally:
                        fobj.close()

                _on_entry(member.size, member.name)

        # A usable restore must have produced a real rootfs directory.
        if not committed:
            # No rootfs content was ever written (manifest-only, empty, or
            # the wrong file): the target was never touched — reject it.
            clear_bar()
            context.error(
                f"Cannot restore: archive does not contain a container "
                f"rootfs. Only archives created by '{PROGRAM_NAME} backup' "
                f"are supported.",
                phase="extract", resource=archive,
            )
            raise IntegrityFailure(
                "backup archive does not contain a container rootfs"
            )

        if not _rootfs_is_a_directory(root_fd):
            # Content was written but it did not yield a real directory at the
            # rootfs path — a stray file, or a symlink standing in for the
            # rootfs (which would also escape the container). Remove the broken
            # result so no rootfs-less container is left behind.
            clear_bar()
            dests.close()
            context.error(
                f"Cannot restore: archive did not produce a valid container "
                f"rootfs. Only archives created by '{PROGRAM_NAME} backup' "
                f"are supported.",
                phase="extract", resource=archive,
            )
            raise IntegrityFailure(
                "backup archive did not produce a valid container rootfs"
            )

        # Apply deferred directory modes now that all writes are done.
        # Reverse order so a parent that ends up unsearchable doesn't
        # block sealing its children.
        for key, name, mode in reversed(deferred_dir_modes):
            dests.chmod(key, name, mode)

        # Write the buffered manifest now that the rootfs is confirmed.
        if pending_manifest is not None:
            _write_manifest(*pending_manifest)

        # The container exists from here on, complete.
        staging.commit(restore_name)

        clear_bar()

        context.info(
            f"Finished restoring the container '{restore_name}'.",
            phase="extract", resource=restore_name,
        )
        succeeded = True

    except KeyboardInterrupt:
        interrupted = True
        clear_bar()
        context.error("Aborted by user.", phase="extract", resource=archive)
        raise
    except (EOFError, tarfile.TarError) as exc:
        clear_bar()
        context.error(
            f"Failed to restore container: {quote_error(exc)}",
            phase="extract", resource=archive,
        )
        context.error(
            "The archive either was corrupted or has unexpected structure.",
            phase="extract", resource=archive,
        )
        raise IntegrityFailure(
            f"failed to restore container: {quote_error(exc)}"
        ) from exc
    except _ArchiveReadError as exc:
        # The archive could not be read: nothing says it is damaged.
        clear_bar()
        message = f"cannot read the archive: {quote_error(exc)}"
        context.error(
            f"Failed to restore container: {message}",
            phase="extract", resource=archive,
        )
        failure = OperationFailure(message)
        failure.reported = True
        raise failure from exc
    except OSError as exc:
        # Writing the container failed -- a full disk, a read-only
        # filesystem -- which says nothing about the archive either.
        clear_bar()
        message = f"cannot write the container: {quote_error(exc)}"
        hint = _WRITE_HINTS.get(exc.errno)
        context.error(
            f"Failed to restore container: {message}",
            phase="extract", resource=archive,
        )
        if hint:
            context.error(hint, phase="extract", resource=archive)
        failure = OperationFailure(f"{message}. {hint}" if hint else message)
        failure.reported = True
        raise failure from exc
    finally:
        try:
            if raw_fh is not None:
                raw_fh.close()
            if dests is not None:
                dests.close()
            if staging is not None:
                # The container was never renamed onto its name, so
                # nothing lists it: what was built goes. Still under the
                # lock, and the lock is released even if the removal is
                # stopped.
                try:
                    if not succeeded:
                        staging.discard(
                            context, phase="extract",
                            resource=restore_name, interrupted=interrupted,
                        )
                finally:
                    staging.close()
        finally:
            if lock is not None:
                lock.release()

    return ArchiveRestore(restore_name, archive)


def _rootfs_is_a_directory(root_fd: int) -> bool:
    """True when the container directory holds a real `rootfs` directory.

    Asked of the descriptor rather than of the path, and with lstat, so a
    symlink standing in for the rootfs is what it is rather than what it
    points at — the check used to run os.path.isdir() on the name *after*
    every member had been written through it.
    """
    try:
        st = dirfd.lstat_at(root_fd, "rootfs")
    except OSError:
        return False
    return stat.S_ISDIR(st.st_mode)


__all__ = ("restore_container",)
