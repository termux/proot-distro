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

from proot_distro.constants import CONTAINERS_DIR
from proot_distro.colors import C, msg
from proot_distro.helpers.download import fmt_size


class _ByteCounter:
    """Thin file wrapper that counts raw bytes passing through read()."""

    def __init__(self, fh):
        self._fh = fh
        self.count = 0

    def read(self, size=-1):
        data = self._fh.read(size)
        self.count += len(data)
        return data

    def readinto(self, buf):
        n = self._fh.readinto(buf)
        self.count += n
        return n

    def __getattr__(self, name):
        return getattr(self._fh, name)


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


def _remove_existing(dest: str, member: tarfile.TarInfo) -> None:
    """Remove any existing filesystem entry at *dest* before extraction."""
    try:
        if os.path.islink(dest) or os.path.isfile(dest):
            os.remove(dest)
        elif os.path.isdir(dest) and not member.isdir():
            shutil.rmtree(dest)
    except OSError:
        pass


def _dest_path(member_name: str) -> tuple:
    """Map a TAR member name to (container_name, dest_path_in_containers).

    Returns (None, None) if the member should be skipped.

    Supports three archive layouts:
      1. New format:    <name>/manifest.json or <name>/rootfs/...
      2. Legacy format: installed-rootfs/<name>/...
      3. No subdir or bare ./: rejected (returns skip sentinel).
    """
    name = member_name.lstrip('/')
    if not name or name in ('.', ''):
        return (None, None)

    parts = name.split('/')

    # Archive starts at root with no real subdirectory — reject.
    if len(parts) == 1 and not name.endswith('/'):
        return (None, None)

    # Legacy format: installed-rootfs/<distro_name>/...
    if parts[0] == _LEGACY_PREFIX:
        if len(parts) < 2:
            return (None, None)
        dist_name = parts[1]
        if len(parts) == 2:
            # Entry is the legacy rootfs dir itself.
            new_path = os.path.join(CONTAINERS_DIR, dist_name, "rootfs")
            return (dist_name, new_path)
        # Re-root: installed-rootfs/<name>/X → containers/<name>/rootfs/X
        rel = '/'.join(parts[2:])
        new_path = os.path.join(CONTAINERS_DIR, dist_name, "rootfs", rel)
        return (dist_name, new_path)

    # New format: <name>/manifest.json or <name>/rootfs/...
    dist_name = parts[0]

    if len(parts) == 1:
        # Top-level container directory entry.
        return (dist_name, os.path.join(CONTAINERS_DIR, dist_name))

    sub = parts[1]

    if sub == "manifest.json" and len(parts) == 2:
        return (dist_name, os.path.join(CONTAINERS_DIR, dist_name, "manifest.json"))

    if sub == "rootfs":
        if len(parts) == 2:
            return (dist_name, os.path.join(CONTAINERS_DIR, dist_name, "rootfs"))
        rel = '/'.join(parts[2:])
        return (dist_name, os.path.join(CONTAINERS_DIR, dist_name, "rootfs", rel))

    # <name>/something_else — treat as going inside rootfs for compatibility.
    rel = '/'.join(parts[1:])
    return (dist_name, os.path.join(CONTAINERS_DIR, dist_name, "rootfs", rel))


def command_restore(args, configs: dict) -> None:  # noqa: ARG001
    archive = getattr(args, "archive", None)
    verbose = getattr(args, "verbose", False)

    if archive:
        if not os.path.exists(archive):
            msg()
            msg(f"{C['BRED']}Error: file "
                f"'{C['YELLOW']}{archive}{C['BRED']}' does not exist.{C['RST']}")
            msg()
            sys.exit(1)
        if os.path.isdir(archive):
            msg()
            msg(f"{C['BRED']}Error: path "
                f"'{C['YELLOW']}{archive}{C['BRED']}' is a directory.{C['RST']}")
            msg()
            sys.exit(1)
    else:
        if sys.stdin.isatty():
            from proot_distro.commands.help import _HELP_COMMANDS
            msg()
            msg(f"{C['BRED']}Error: archive file path is not specified and it "
                f"looks like nothing is being piped via stdin "
                f"either.{C['RST']}")
            _HELP_COMMANDS["restore"]()
            sys.exit(1)

    os.makedirs(CONTAINERS_DIR, exist_ok=True)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Extracting container from the archive...{C['RST']}")

    use_tty = sys.stderr.isatty()
    done_size = 0
    total_size = 0
    counter = None
    cleared: set = set()

    def _on_entry(member_size: int, member_name: str) -> None:
        nonlocal done_size
        done_size += member_size
        if verbose:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Extracting: '{member_name}'{C['RST']}")
        if not use_tty:
            return
        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        if counter is not None and total_size:
            done = counter.count
            pct = min(done * 100 // total_size, 100)
            bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
            sys.stderr.write(
                f"\r{pfx}[{bar}] {pct:3d}%  "
                f"{fmt_size(done)} / {fmt_size(total_size)}\033[K{C['RST']}"
            )
        else:
            sys.stderr.write(
                f"\r{pfx}{fmt_size(done_size)} extracted...\033[K{C['RST']}"
            )
        sys.stderr.flush()

    def _check_bare_root(member_name: str) -> bool:
        """Return True if this member has no real subdirectory (reject)."""
        name = member_name.lstrip('/')
        if not name:
            return False
        parts = name.split('/')
        return len(parts) == 1 and not name.endswith('/')

    raw_fh = None
    try:
        if archive:
            total_size = os.path.getsize(archive)
            raw_fh = open(archive, 'rb')
            counter = _ByteCounter(raw_fh)
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
                    if use_tty:
                        sys.stderr.write("\r\033[K")
                        sys.stderr.flush()
                    msg()
                    msg(f"{C['BRED']}Error: archive is not compatible "
                        f"with proot-distro. Files must be stored under "
                        f"a container name subdirectory "
                        f"(e.g. ubuntu/rootfs/...).{C['RST']}")
                    msg()
                    sys.exit(1)

                dist_name, dest = _dest_path(member.name)
                if dist_name is None:
                    continue

                # On the first entry for a given container's rootfs, clear it.
                if dist_name not in cleared:
                    rootfs_dir = os.path.join(
                        CONTAINERS_DIR, dist_name, "rootfs"
                    )
                    if os.path.isdir(rootfs_dir):
                        pfx = (
                            f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                            f"{C['CYAN']}"
                        )
                        count = 0
                        if use_tty:
                            sys.stderr.write("\r\033[K")
                            sys.stderr.flush()
                        for dp, dns, fns in os.walk(
                            rootfs_dir, topdown=False, followlinks=False
                        ):
                            for fname in fns:
                                try:
                                    os.unlink(os.path.join(dp, fname))
                                except OSError:
                                    pass
                                count += 1
                                if use_tty:
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
                        if use_tty:
                            sys.stderr.write("\r\033[K")
                            sys.stderr.flush()
                    cleared.add(dist_name)

                _remove_existing(dest, member)

                if member.isdir():
                    os.makedirs(dest, exist_ok=True)
                    try:
                        os.chmod(dest, stat.S_IMODE(member.mode))
                    except OSError:
                        pass

                elif member.issym():
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    os.symlink(member.linkname, dest)

                elif member.islnk():
                    # Resolve hard link within the containers dir.
                    link_src_name, link_src = _dest_path(member.linkname)
                    if link_src is None:
                        continue
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    try:
                        os.link(link_src, dest)
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

        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Finished restoring the container.{C['RST']}")

    except KeyboardInterrupt:
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Aborted by user.{C['RST']}")
        sys.exit(1)
    except (EOFError, OSError, tarfile.TarError) as exc:
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Failed to restore container: {exc}{C['RST']}")
        msg()
        msg(f"{C['BRED']}The archive may be corrupted or was not created by "
            f"PRoot-Distro.{C['RST']}")
        msg()
        sys.exit(1)
    finally:
        if raw_fh is not None:
            raw_fh.close()
