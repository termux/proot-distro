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
# Legacy archives with installed-rootfs/<name> layout are also accepted:
# contents are re-rooted to containers/<name>/rootfs/. Archives with no
# subdirectory are rejected. Compression is auto-detected via tarfile r|*
# (archive file) or from header magic bytes (stdin). For file input,
# progress is tracked in compressed bytes consumed so total_size is
# os.path.getsize() — instant, no upfront scan needed.

import os
import shutil
import stat
import sys
import tarfile

from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.message import (
    C, msg, log_info, log_error, crit_error,
)
from proot_distro.progress import (
    ByteCounter, clear_bar, draw_bytes_bar, progress_active,
)
from proot_distro.commands.help import HELP_COMMANDS
from proot_distro.locking import (
    ContainerLock, container_lock_path, read_lock_info,
)
from proot_distro.names import is_valid_name
from proot_distro.paths import (
    container_dir, container_manifest, container_rootfs,
)


# Magic-byte signatures used to identify compressed streams.
_MAGIC_COMPRESS = (
    (b'\x1f\x8b',      'gz'),   # gzip
    (b'BZh',           'bz2'),  # bzip2
    (b'\xfd7zXZ\x00',  'xz'),   # xz
    (b'\x5d\x00',      'xz'),   # lzma legacy (lzma.open handles both)
)

# Legacy archive prefix.
_LEGACY_PREFIX = "installed-rootfs"


def _detect_compression(header: bytes) -> str:
    """Return the tarfile mode suffix inferred from *header* magic bytes."""
    for magic, mode in _MAGIC_COMPRESS:
        if header.startswith(magic):
            return mode
    return ''


def _clear_existing_rootfs(container_name: str) -> None:
    """Remove the destination rootfs before extracting a new copy.

    Streams a `Removing old rootfs... N files` counter to stderr so
    the user gets feedback during long-running clears (multi-GB rootfs
    on slow flash).
    """
    rootfs_dir = container_rootfs(container_name)
    if not os.path.isdir(rootfs_dir):
        return
    pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
    count = 0
    clear_bar()
    for dp, dns, fns in os.walk(rootfs_dir, topdown=False, followlinks=False):
        for fname in fns:
            try:
                os.unlink(os.path.join(dp, fname))
            except OSError:
                pass
            count += 1
            if progress_active():
                sys.stderr.write(
                    f"\r{pfx}Removing old rootfs..."
                    f" {count} files{C['RST']}"
                )
                sys.stderr.flush()
        for dname in dns:
            try:
                os.rmdir(os.path.join(dp, dname))
            except OSError:
                pass
    shutil.rmtree(rootfs_dir, ignore_errors=True)
    clear_bar()


def _remove_existing(dest: str, member: tarfile.TarInfo) -> None:
    """Remove any existing filesystem entry at *dest* before extraction."""
    try:
        if os.path.islink(dest) or os.path.isfile(dest):
            os.remove(dest)
        elif os.path.isdir(dest) and not member.isdir():
            shutil.rmtree(dest)
    except OSError:
        pass


_SKIP = (None, None)


def _dest_path(member_name: str) -> tuple:
    """Map a TAR member name to (container_name, dest_path_in_containers).

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
        rest = parts[2:]
        if not rest:
            return (container_name, container_rootfs(container_name))
        return (container_name, os.path.join(container_rootfs(container_name), *rest))

    # New format: <name>/...
    container_name = parts[0]
    if not is_valid_name(container_name):
        return _SKIP

    if len(parts) == 1:
        return (container_name, container_dir(container_name))

    sub = parts[1]
    rest = parts[2:]

    if sub == "manifest.json" and not rest:
        return (container_name, container_manifest(container_name))

    if sub == "rootfs":
        if not rest:
            return (container_name, container_rootfs(container_name))
        return (container_name, os.path.join(container_rootfs(container_name), *rest))

    # <name>/<anything_else>  -> treated as a path inside rootfs for
    # back-compat with archives created by very old versions.
    return (container_name, os.path.join(container_rootfs(container_name), *parts[1:]))


def command_restore(args) -> None:
    """Reinstate one or more containers from a tar backup."""
    archive = getattr(args, "archive", None)
    verbose = getattr(args, "verbose", False)

    if archive:
        if not os.path.exists(archive):
            crit_error(f"file '{archive}' does not exist.")
            sys.exit(1)
        if os.path.isdir(archive):
            crit_error(f"path '{archive}' is a directory.")
            sys.exit(1)
        if not os.access(archive, os.R_OK):
            crit_error(f"file '{archive}' is not readable.")
            sys.exit(1)
    else:
        if sys.stdin.isatty():
            msg()
            crit_error("archive file path is not specified and "
                       "nothing is being piped via stdin.")
            HELP_COMMANDS["restore"]()
            sys.exit(1)

    os.makedirs(CONTAINERS_DIR, exist_ok=True)

    log_info("Restoring container from the backup...")

    done_size = 0
    total_size = 0
    counter = None
    cleared: set = set()

    def _on_entry(member_size: int, member_name: str) -> None:
        nonlocal done_size
        done_size += member_size
        if verbose:
            log_info(f"Extracting: '{member_name}'")
        if counter is not None and total_size:
            draw_bytes_bar(counter.count, total_size)
        else:
            draw_bytes_bar(done_size, 0, noun="extracted")

    def _check_bare_root(member_name: str) -> bool:
        """Return True if this member has no real subdirectory (reject)."""
        name = member_name.lstrip('/')
        if not name:
            return False
        parts = name.split('/')
        return len(parts) == 1 and not name.endswith('/')

    raw_fh = None
    # Per-container exclusive locks acquired lazily on first member encounter,
    # before any modification to that container's rootfs.
    pending_locks: dict = {}
    # Dirs whose archived mode lacks owner rwx: temporarily widened so we
    # can write into them, with the final chmod deferred until extraction
    # finishes. Applied in reverse insertion order so children are sealed
    # before their parents.
    deferred_dir_modes: list = []
    try:
        if archive:
            total_size = os.path.getsize(archive)
            raw_fh = open(archive, 'rb')
            counter = ByteCounter(raw_fh)
            tf_kwargs = dict(fileobj=counter, mode='r|*')
        else:
            header = sys.stdin.buffer.peek(6)[:6]
            comp = _detect_compression(header)
            tf_kwargs = dict(fileobj=sys.stdin.buffer, mode=f'r|{comp}')

        with tarfile.open(**tf_kwargs) as tf:
            for member in tf:
                if member.isblk() or member.ischr() or member.isfifo():
                    continue

                if _check_bare_root(member.name):
                    clear_bar()
                    log_error(f"Cannot restore: provided file has invalid "
                              f"structure. Only archives created by "
                              f"'{PROGRAM_NAME} backup' are supported.")
                    sys.exit(1)

                container_name, dest = _dest_path(member.name)
                if container_name is None:
                    continue

                # Acquire exclusive lock for this container before
                # touching it. Restore archives are streamed so we can't
                # pre-scan the names; lock lazily on first sighting.
                if container_name not in pending_locks:
                    lock = ContainerLock(
                        container_name, exclusive=True, command="restore"
                    )
                    if not lock.acquire():
                        hint = read_lock_info(container_lock_path(container_name))
                        clear_bar()
                        log_error(f"Cannot restore: container "
                                  f"'{container_name}' is busy{hint}.")
                        sys.exit(1)
                    pending_locks[container_name] = lock

                # On the first entry for a given container's rootfs, clear it.
                if container_name not in cleared:
                    _clear_existing_rootfs(container_name)
                    cleared.add(container_name)

                _remove_existing(dest, member)

                if member.isdir():
                    os.makedirs(dest, exist_ok=True)
                    mode = stat.S_IMODE(member.mode)
                    if (mode & stat.S_IRWXU) != stat.S_IRWXU:
                        try:
                            os.chmod(dest, mode | stat.S_IRWXU)
                        except OSError:
                            pass
                        deferred_dir_modes.append((dest, mode))
                    else:
                        try:
                            os.chmod(dest, mode)
                        except OSError:
                            pass

                elif member.issym():
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    os.symlink(member.linkname, dest)

                elif member.islnk():
                    # Copy hard-linked files rather than recreating the link,
                    # since containers use proot's --link2symlink and hard
                    # links on the host fs would share inodes across what the
                    # guest treats as independent files.
                    _, link_src = _dest_path(member.linkname)
                    if link_src is None:
                        continue
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    try:
                        shutil.copy2(link_src, dest)
                        if member.mode:
                            try:
                                os.chmod(dest, stat.S_IMODE(member.mode))
                            except OSError:
                                pass
                    except OSError:
                        pass

                elif member.isreg():
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        continue
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    try:
                        with open(dest, 'wb') as out:
                            while True:
                                chunk = fobj.read(1 << 17)  # 128 KiB
                                if not chunk:
                                    break
                                out.write(chunk)
                        try:
                            os.chmod(dest, stat.S_IMODE(member.mode))
                        except OSError:
                            pass
                    finally:
                        fobj.close()

                else:
                    continue

                _on_entry(member.size, member.name)

        # Apply deferred directory modes now that all writes are done.
        # Reverse order so a parent that ends up unsearchable doesn't
        # block sealing its children.
        for path, mode in reversed(deferred_dir_modes):
            try:
                os.chmod(path, mode)
            except OSError:
                pass

        clear_bar()

        log_info("Finished restoring the container.")

    except KeyboardInterrupt:
        clear_bar()
        log_error("Aborted by user.")
        sys.exit(1)
    except (EOFError, OSError, tarfile.TarError) as exc:
        clear_bar()
        log_error(f"Failed to restore container: {exc}")
        log_error(f"{C['BRED']}The archive either was corrupted or has "
                  f"unexpected structure.{C['RST']}")
        sys.exit(1)
    finally:
        if raw_fh is not None:
            raw_fh.close()
        for lock in pending_locks.values():
            lock.release()
