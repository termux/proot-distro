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
# Compression is detected from file header magic bytes. For seekable file
# input, member count is pre-collected so an accurate progress percentage
# can be shown. For stdin streaming, only a counter is shown. Old rootfs
# contents are cleared before extraction so the result exactly matches
# the archive.

import os
import shutil
import stat
import sys
import tarfile

from proot_distro.constants import INSTALLED_ROOTFS_DIR
from proot_distro.colors import C, msg


# Magic-byte signatures used to identify compressed streams.
_MAGIC_COMPRESS = (
    (b'\x1f\x8b',      'gz'),   # gzip
    (b'BZh',           'bz2'),  # bzip2
    (b'\xfd7zXZ\x00',  'xz'),   # xz
    (b'\x5d\x00',      'xz'),   # lzma legacy (lzma.open handles both)
)


def _detect_compression(header: bytes) -> str:
    """Return the tarfile compression mode inferred from *header* magic bytes."""
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

    rootfs_parent = os.path.dirname(INSTALLED_ROOTFS_DIR)
    rootfs_base = os.path.basename(INSTALLED_ROOTFS_DIR)

    os.makedirs(INSTALLED_ROOTFS_DIR, exist_ok=True)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Extracting rootfs from the archive...{C['RST']}")

    use_tty = sys.stderr.isatty()
    done = 0
    cleared_rootfs: set = set()

    def _on_entry(total: int, member_name: str) -> None:
        nonlocal done
        done += 1
        if verbose:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Extracting: '{member_name}'{C['RST']}")
        if not use_tty:
            return
        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        if total:
            pct = done * 100 // total
            bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
            sys.stderr.write(
                f"\r{pfx}[{bar}] {pct:3d}%  {done} / {total} files{C['RST']}"
            )
        else:
            sys.stderr.write(
                f"\r{pfx}{done} files extracted...{C['RST']}"
            )
        sys.stderr.flush()

    try:
        if archive:
            with open(archive, 'rb') as fh:
                header = fh.read(6)
            comp = _detect_compression(header)
            tar_mode = f'r:{comp}' if comp else 'r:*'
            open_kwargs = dict(name=archive, mode=tar_mode)
        else:
            header = sys.stdin.buffer.peek(6)[:6]
            comp = _detect_compression(header)
            tar_mode = f'r|{comp}'
            open_kwargs = dict(fileobj=sys.stdin.buffer, mode=tar_mode)

        with tarfile.open(**open_kwargs) as tf:
            if archive:
                if use_tty:
                    sys.stderr.write(
                        f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                        f"{C['CYAN']}Estimating progress...{C['RST']}"
                    )
                    sys.stderr.flush()
                all_members = [
                    m for m in tf.getmembers()
                    if not (m.isblk() or m.ischr() or m.isfifo())
                ]
                total = len(all_members)
                if use_tty:
                    sys.stderr.write("\r\033[K")
                    sys.stderr.flush()
            else:
                all_members = tf
                total = 0

            for member in all_members:
                if not archive and (member.isblk() or member.ischr() or member.isfifo()):
                    continue

                # Route only installed-rootfs/ entries; silently skip all
                # else (including legacy proot-distro/ config entries).
                name = member.name.lstrip('/')
                if not (name.startswith(rootfs_base + '/') or name == rootfs_base):
                    continue

                dest = os.path.join(rootfs_parent, name)

                # On the first entry for a given distro's rootfs, clear the
                # existing rootfs directory so no stale files remain.
                rel = os.path.relpath(dest, INSTALLED_ROOTFS_DIR)
                parts = rel.split(os.sep)
                if (parts and parts[0] not in ('', '..')
                        and parts[0] not in cleared_rootfs):
                    old_dir = os.path.join(INSTALLED_ROOTFS_DIR, parts[0])
                    if os.path.isdir(old_dir):
                        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                        count = 0
                        if use_tty:
                            sys.stderr.write("\r\033[K")
                            sys.stderr.flush()
                        for dp, dns, fns in os.walk(
                            old_dir, topdown=False, followlinks=False
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
                        shutil.rmtree(old_dir, ignore_errors=True)
                        if use_tty:
                            sys.stderr.write("\r\033[K")
                            sys.stderr.flush()
                    cleared_rootfs.add(parts[0])

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
                    link_src = os.path.join(
                        rootfs_parent, member.linkname.lstrip('/')
                    )
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

                _on_entry(total, member.name)

        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Finished restoring the distribution.{C['RST']}")

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
            f"Failed to restore distribution: {exc}{C['RST']}")
        msg()
        msg(f"{C['BRED']}The archive may be corrupted or was not created by "
            f"PRoot-Distro.{C['RST']}")
        msg()
        sys.exit(1)
