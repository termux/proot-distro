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

# Architecture: Single streaming tar -> rootfs extractor used by both
# Docker layer application and plain rootfs tarball installs. The two
# call sites used to be near-identical 130/150-line loops; here they
# share one implementation and differ only by two parameters:
#
#   strip             — leading path components to drop from each
#                       member name (>0 only for plain rootfs tarballs
#                       whose entries live under a wrapper directory).
#   handle_whiteouts  — when True, OCI whiteouts (.wh.<name>, opaque
#                       .wh..wh..opq) consume sibling entries; when
#                       False they're silently skipped.
#
# Invariants the loop maintains:
#
#   - Block/char/FIFO/socket entries are skipped.
#   - Members containing ".." or empty components after strip are
#     dropped so a crafted archive cannot escape the rootfs. Bare "."
#     components are kept (OCI layers commonly use "./foo" paths);
#     os.path.join collapses them so they cannot escape either.
#   - Hard-link targets (member.linkname) get the same filtering as
#     member.name. Without it, a malicious archive could set linkname
#     to "../../etc/shadow" and we'd shutil.copy2 the host's file into
#     a member-defined dest inside the rootfs.
#   - Hard links are deferred until every regular file has been
#     written, then copied with shutil.copy2 so the link source
#     definitely exists and mtime survives the round-trip.
#   - Directory mtimes are stamped last (writing into a dir bumps its
#     mtime, so this must follow all writes).
#   - Directories get at least S_IRWXU so subsequent writes succeed
#     even when the archive recorded a stricter mode.
#   - Progress is tracked in compressed bytes consumed via ByteCounter
#     so the denominator is os.path.getsize() and no upfront scan is
#     needed.

import os
import shutil
import stat
import tarfile

from proot_distro.progress import ByteCounter, clear_bar, draw_bytes_bar


def extract_tar_to_rootfs(
    archive_path: str,
    rootfs_dir: str,
    *,
    strip: int = 0,
    handle_whiteouts: bool = False,
) -> None:
    """Stream-extract *archive_path* into *rootfs_dir*.

    See module docstring for the shared invariants. The function
    consumes a compressed-or-not tar stream via tarfile's `'r|*'`
    auto-detect, so it works for raw tar, .tar.gz, .tar.bz2, .tar.xz,
    and a Docker/OCI layer blob alike.
    """
    total_size = os.path.getsize(archive_path)
    deferred_links: list = []  # (dest, src) — copied after all regular files
    deferred_dirs: list = []   # (dest, mtime) — stamped after all writes

    with open(archive_path, "rb") as raw_fh:
        counter = ByteCounter(raw_fh)
        with tarfile.open(fileobj=counter, mode="r|*") as tf:
            for member in tf:
                _process_member(
                    member, tf, rootfs_dir,
                    strip=strip,
                    handle_whiteouts=handle_whiteouts,
                    deferred_links=deferred_links,
                    deferred_dirs=deferred_dirs,
                )
                draw_bytes_bar(counter.count, total_size)

    # All regular files written; now copy hard links. shutil.copy2
    # preserves mtime, which was already set above.
    for dest, src in deferred_links:
        if os.path.lexists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        if os.path.isfile(src):
            try:
                shutil.copy2(src, dest)
            except OSError:
                pass

    # Stamp directory mtimes last (writing files into a dir bumps it).
    for path, mtime in reversed(deferred_dirs):
        try:
            os.utime(path, (mtime, mtime))
        except OSError:
            pass

    clear_bar()


# ----- per-member dispatch -------------------------------------------------

def _process_member(member, tf, rootfs_dir, *, strip, handle_whiteouts,
                    deferred_links, deferred_dirs):
    if member.isblk() or member.ischr() or member.isfifo():
        return

    parts = member.name.lstrip("/").rstrip("/").split("/")
    if len(parts) <= strip:
        return
    rel_parts = parts[strip:]
    if any(p in ("..", "") for p in rel_parts):
        return

    rel_path = "/".join(rel_parts)
    if not rel_path or rel_path == ".":
        return

    parent = (
        os.path.join(rootfs_dir, *rel_parts[:-1])
        if len(rel_parts) > 1 else rootfs_dir
    )
    dest = os.path.join(rootfs_dir, rel_path)

    if handle_whiteouts and _apply_whiteout(rel_parts, parent):
        return

    os.makedirs(parent, exist_ok=True)

    if member.isdir():
        os.makedirs(dest, exist_ok=True)
        try:
            os.chmod(dest, stat.S_IMODE(member.mode) | stat.S_IRWXU)
        except OSError:
            pass
        deferred_dirs.append((dest, member.mtime))

    elif member.issym():
        _write_symlink(dest, member)

    elif member.islnk():
        _defer_hardlink(member, rootfs_dir, strip, dest, deferred_links)

    elif member.isreg():
        _write_regular(dest, member, tf)


def _apply_whiteout(rel_parts, parent) -> bool:
    """Handle an OCI whiteout member. Returns True iff a whiteout was applied."""
    basename = rel_parts[-1]
    if basename == ".wh..wh..opq":
        # Opaque whiteout: clear everything inside the parent dir.
        if os.path.isdir(parent):
            for entry in os.listdir(parent):
                _remove_fstree(os.path.join(parent, entry))
        return True
    if basename.startswith(".wh."):
        # Regular whiteout: delete the named sibling.
        _remove_fstree(os.path.join(parent, basename[4:]))
        return True
    return False


def _remove_fstree(path: str) -> None:
    """Remove a file, symlink, or directory tree; ignore all errors."""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
    except OSError:
        pass


def _write_symlink(dest: str, member) -> None:
    if os.path.lexists(dest):
        _remove_fstree(dest)
    try:
        os.symlink(member.linkname, dest)
    except OSError:
        return
    try:
        os.utime(dest, (member.mtime, member.mtime), follow_symlinks=False)
    except OSError:
        pass


def _defer_hardlink(member, rootfs_dir, strip, dest, deferred_links):
    """Queue a hardlink for copy after all regular files are written.

    The linkname is filtered identically to member.name: leading slashes
    are stripped, the first `strip` components dropped, and any ".." or
    empty component drops the entry. Without this a malicious archive
    could point linkname at a host path (e.g. "../../etc/shadow") and
    shutil.copy2 would resolve it through the rootfs prefix, copying
    host content into the member-defined dest inside the rootfs.
    """
    lparts = member.linkname.lstrip("/").rstrip("/").split("/")
    if len(lparts) <= strip:
        return
    rel_lparts = lparts[strip:]
    if any(p in ("..", "") for p in rel_lparts):
        return
    link_src = os.path.join(rootfs_dir, *rel_lparts)
    deferred_links.append((dest, link_src))


def _write_regular(dest: str, member, tf) -> None:
    fobj = tf.extractfile(member)
    if fobj is None:
        return
    if os.path.lexists(dest):
        try:
            os.remove(dest)
        except OSError:
            pass
    try:
        with open(dest, "wb") as out:
            shutil.copyfileobj(fobj, out, 1 << 17)  # 128 KiB chunks
        try:
            os.chmod(dest, stat.S_IMODE(member.mode))
        except OSError:
            pass
        try:
            os.utime(dest, (member.mtime, member.mtime))
        except OSError:
            pass
    finally:
        fobj.close()
