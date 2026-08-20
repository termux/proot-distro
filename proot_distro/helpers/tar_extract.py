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
#     dropped so a crafted archive cannot escape the rootfs. Interior "."
#     components are kept (OCI layers commonly use "./foo" paths) and
#     _safe_resolve drops them on the way through; a *trailing* one is
#     dropped with the member, since it names the parent directory rather
#     than an entry and the writers would have acted on that.
#   - A whiteout deletes the name after its ".wh." prefix, and that name
#     is held to the same rule: ".wh..." spells ".." and reached above
#     the extraction root (see _apply_whiteout).
#   - Every destination's parent is resolved through any pre-existing
#     symlink components with each hop clamped inside the rootfs
#     (_safe_resolve): absolute symlink targets are re-rooted at the
#     rootfs and ".." can never ascend past it. Without this an earlier
#     member could ship `evil -> /` (or `evil -> ../../`) and a later
#     `evil/passwd` would be written *through* that symlink onto the
#     host. Re-rooting mirrors proot's runtime view (guest '/' is the
#     rootfs), so a legitimate absolute symlink still resolves to the
#     right in-rootfs location instead of escaping.
#   - Hard-link targets (member.linkname) get the same filtering and
#     clamped resolution as member.name. Without it a malicious archive
#     could set linkname to "../../etc/shadow" (or route it through a
#     planted symlink) and we'd shutil.copy2 the host's file into a
#     member-defined dest inside the rootfs.
#   - Hard links are deferred until every regular file has been
#     written, then both endpoints are re-resolved (so a symlink
#     planted *after* the link member can't redirect the copy) and
#     copied with shutil.copy2 so the link source definitely exists and
#     mtime survives the round-trip.
#   - Directory mtimes are stamped last (writing into a dir bumps its
#     mtime, so this must follow all writes).
#   - Directories get at least S_IRWXU so subsequent writes succeed
#     even when the archive recorded a stricter mode.
#   - Progress is tracked in compressed bytes consumed via ByteCounter
#     so the denominator is os.path.getsize() and no upfront scan is
#     needed.

import hashlib
import os
import shutil
import stat
import tarfile

from proot_distro import dirfd
from proot_distro.compress import (
    require_read_support, require_read_support_fd,
)
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
    .tar.zst (Python 3.14+), and a Docker/OCI layer blob alike.
    """
    # Auto-detect only recognises zstd from Python 3.14 on; without it
    # the stream reads as a truncated tar, so say what it really is.
    require_read_support(archive_path, f"archive '{archive_path}'")

    with open(archive_path, "rb") as raw_fh:
        _extract_stream(
            raw_fh, os.path.getsize(archive_path), rootfs_dir,
            strip=strip, handle_whiteouts=handle_whiteouts,
        )


class _HashingReader:
    """Stream wrapper that hashes every byte drawn through it."""

    def __init__(self, fh):
        self._fh = fh
        self.hasher = hashlib.sha256()

    def read(self, size=-1):
        data = self._fh.read(size)
        self.hasher.update(data)
        return data

    def readinto(self, buf):
        n = self._fh.readinto(buf)
        if n:
            self.hasher.update(memoryview(buf)[:n])
        return n

    def drain(self) -> None:
        """Pull whatever is left so the hash covers the whole stream.

        tarfile stops at the end-of-archive marker, so the tail of the
        *compressed* file is never pulled through on its own — and a
        digest over a prefix is not a digest over the blob.
        """
        while self._fh.read(1 << 20):
            pass

    def __getattr__(self, name):
        return getattr(self._fh, name)


def extract_tar_fd_to_rootfs(
    fd: int,
    rootfs_dir: str,
    *,
    strip: int = 0,
    handle_whiteouts: bool = False,
    subject: str = "archive",
    expected_sha256: str = "",
) -> None:
    """Stream-extract the archive behind *fd* into *rootfs_dir*.

    The same extraction, reading a descriptor rather than a name. Naming
    a blob twice — once to hash it, once to read it — is what leaves room
    for it to change in between, and on Termux that room is real:
    LAYER_CACHE_DIR sits under the `$TERMUX_PREFIX` bound read-write into
    every non-isolated container, so a session running alongside an
    install can reach it. A descriptor settles which *inode* is read.

    It does not settle which *bytes*: an inode can be truncated and
    rewritten in place just as easily as a name can be re-pointed. So
    with *expected_sha256* the bytes are hashed **as they are consumed**
    and the extraction raises if the total does not match. That is the
    check that actually covers the read, the pre-hash upstream being what
    decides whether to use the cache entry at all (evict and refetch, or
    refuse). The hex is passed in rather than a digest string so this
    module stays clear of helpers.docker, which imports it.

    The failure is raised after the fact — the members are already on the
    rootfs by the time the last byte proves the archive wrong. Every
    caller discards the tree on error: `install` removes the container
    directory, `build` its temporary stage.

    The descriptor stays open and its position is left at the end;
    callers own it.
    """
    require_read_support_fd(fd, subject)
    os.lseek(fd, 0, os.SEEK_SET)
    raw_fh = open(fd, "rb", closefd=False)
    hashing = _HashingReader(raw_fh) if expected_sha256 else raw_fh
    try:
        _extract_stream(
            hashing, os.fstat(fd).st_size, rootfs_dir,
            strip=strip, handle_whiteouts=handle_whiteouts,
        )
        if expected_sha256:
            hashing.drain()
            actual = hashing.hasher.hexdigest()
            if actual != expected_sha256:
                raise RuntimeError(
                    f"{subject} does not match its digest "
                    f"(expected sha256:{expected_sha256}, read "
                    f"sha256:{actual}). The blob changed while it was "
                    f"being applied."
                )
    finally:
        raw_fh.close()


def _extract_stream(raw_fh, total_size, rootfs_dir, *, strip,
                    handle_whiteouts) -> None:
    """The extraction proper, over an already-open binary stream."""
    deferred_links: list = []  # (dest, src) — copied after all regular files
    deferred_dirs: list = []   # (dest, mtime) — stamped after all writes

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
    # preserves mtime, which was already set above. Both endpoints are
    # re-resolved here (not at defer time) so a symlink planted by a
    # later member can't redirect either the read source or the write
    # dest outside the rootfs.
    for dest_parts, src_parts in deferred_links:
        parent = _safe_resolve(rootfs_dir, dest_parts[:-1])
        if parent is None:
            continue
        dest = os.path.join(parent, dest_parts[-1])
        src = _safe_resolve(rootfs_dir, src_parts)
        if src is None:
            continue
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
    if not rel_path or rel_parts[-1] == os.curdir:
        # A trailing '.' names the directory the member already sits in
        # rather than an entry of its own, and os.path.join keeps it in
        # the path, so the writers below acted on that directory: a
        # symlink member rmtree'd its whole contents before failing on
        # EEXIST, and a regular one ended the extraction on EISDIR.
        # Interior '.' components stay allowed — OCI layers spell their
        # paths './foo' as a matter of course and _safe_resolve drops
        # them on the way through.
        return

    # Resolve the destination's parent through any pre-existing symlink
    # components, clamping every hop inside rootfs_dir (see module
    # docstring). The final component is deliberately *not* followed so
    # we operate on the entry itself, never on whatever a same-named
    # symlink points at.
    parent = _safe_resolve(rootfs_dir, rel_parts[:-1])
    if parent is None:
        return
    dest = os.path.join(parent, rel_parts[-1])

    if handle_whiteouts and _apply_whiteout(rel_parts, parent):
        return

    os.makedirs(parent, exist_ok=True)

    if member.isdir():
        # A symlink already occupying this name would make os.makedirs
        # (and the chmod/utime below) act on its target, so drop it
        # first — overlay semantics replace a symlink with a real dir.
        if os.path.islink(dest):
            _remove_fstree(dest)
        os.makedirs(dest, exist_ok=True)
        try:
            os.chmod(dest, stat.S_IMODE(member.mode) | stat.S_IRWXU)
        except OSError:
            pass
        deferred_dirs.append((dest, member.mtime))

    elif member.issym():
        _write_symlink(dest, member)

    elif member.islnk():
        _defer_hardlink(member, strip, rel_parts, deferred_links)

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
        # Regular whiteout: delete the named sibling. What is deleted is
        # the part after the prefix, and it has to name a sibling: `.wh...`
        # slices to '..', which os.path.join leaves in the path and
        # _remove_fstree then rmtree's — the parent's parent, which for a
        # whiteout at the top of a layer is one level *above* the
        # extraction root. A single such member emptied
        # containers/<name>/, manifest and rootfs together, during an
        # install of a crafted image. `.wh.` and `.wh..` slice to '' and
        # '.', which name the parent itself and cost it its contents.
        # None of the three names a sibling, so there is nothing for the
        # whiteout to delete; the member is still consumed, since an entry
        # called `.wh.*` is not one to write into the rootfs either.
        target = basename[4:]
        if target not in ("", os.curdir, os.pardir):
            _remove_fstree(os.path.join(parent, target))
        return True
    return False


def _remove_fstree(path: str) -> None:
    """Remove a file, symlink, or directory tree; ignore all errors.

    Reached from both whiteout forms and from a member that replaces one,
    so what it is pointed at is a directory an *earlier layer* wrote — as
    deep and as sealed as the image chose. shutil.rmtree() recursed, and
    RecursionError is not an OSError, so a crafted image that put a deep
    tree in one layer and a whiteout in the next took `install` down with
    a traceback. remove_tree() also unlinks a symlink rather than
    traversing it, which is the type test this used to make by hand.
    """
    dirfd.remove_tree(path)


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


def _defer_hardlink(member, strip, rel_parts, deferred_links):
    """Queue a hardlink for copy after all regular files are written.

    The linkname is filtered identically to member.name: leading slashes
    are stripped, the first `strip` components dropped, and any ".." or
    empty component drops the entry. Without this a malicious archive
    could point linkname at a host path (e.g. "../../etc/shadow") and
    shutil.copy2 would resolve it through the rootfs prefix, copying
    host content into the member-defined dest inside the rootfs.

    Only the (validated) relative components of both endpoints are
    stored; the on-disk paths are resolved with _safe_resolve at copy
    time so a symlink planted by a later member can't redirect the read
    source or the write dest out of the rootfs.
    """
    lparts = member.linkname.lstrip("/").rstrip("/").split("/")
    if len(lparts) <= strip:
        return
    rel_lparts = lparts[strip:]
    if any(p in ("..", "") for p in rel_lparts):
        return
    deferred_links.append((rel_parts, rel_lparts))


def _safe_resolve(root, parts):
    """Resolve *parts* beneath *root*, clamping every hop inside it.

    Returns an absolute path guaranteed to live within *root*, or None
    if a symlink loop / excessive chain is hit (caller skips the entry).
    See safe_resolve_parts, which does the work.
    """
    resolved = safe_resolve_parts(root, parts)
    if resolved is None:
        return None
    return os.path.join(root, *resolved)


def safe_resolve_parts(root, parts):
    """The components *parts* resolves to beneath *root*, or None.

    Walks *parts* component by component starting at *root*. Existing
    symlink components are followed, but their targets are interpreted
    relative to *root*: an absolute target re-roots at *root* and ".."
    can never ascend above it. This both blocks symlink-traversal
    escapes and matches proot's runtime view, where the guest '/' is
    the rootfs, so legitimate absolute symlinks resolve to the right
    in-rootfs location. Components that don't exist yet are taken
    verbatim (a not-yet-written subtree can't be a symlink).

    Pass parent components only when the final element must not be
    followed (file/dir/symlink writes); pass the full path to resolve a
    hardlink's source file.

    The components come back rather than a joined path for a caller that
    means to descend them with openat(2): the walk says where the entry
    belongs, but it resolves each level by name, so a component
    re-pointed afterwards would still be followed by whatever acts on
    the result. Only re-walking the answer off a descriptor closes that.
    """
    resolved: list = []
    pending = list(parts)
    link_budget = 40
    while pending:
        comp = pending.pop(0)
        if comp in ("", "."):
            continue
        if comp == "..":
            if resolved:
                resolved.pop()
            continue
        current = os.path.join(root, *resolved, comp)
        try:
            st = os.lstat(current)
        except OSError:
            # Doesn't exist yet (or unreadable) — safe to take as-is.
            resolved.append(comp)
            continue
        if stat.S_ISLNK(st.st_mode):
            link_budget -= 1
            if link_budget < 0:
                return None
            try:
                target = os.readlink(current)
            except OSError:
                return None
            tparts = target.split("/")
            if target.startswith("/"):
                resolved = []  # absolute target: re-root at *root*
            pending[:0] = tparts
        else:
            resolved.append(comp)
    return resolved


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
