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
#     (safe_resolve_parts): absolute symlink targets are re-rooted at the
#     rootfs and ".." can never ascend past it. Without this an earlier
#     member could ship `evil -> /` (or `evil -> ../../`) and a later
#     `evil/passwd` would be written *through* that symlink onto the
#     host. Re-rooting mirrors proot's runtime view (guest '/' is the
#     rootfs), so a legitimate absolute symlink still resolves to the
#     right in-rootfs location instead of escaping.
#   - Resolving says where a member *belongs*; it does not make writing
#     there safe, because it decides by name and everything that acted on
#     the answer named it again -- os.makedirs, os.remove, open(dest),
#     os.chmod, os.utime each resolved the path afresh. The extraction
#     therefore takes a **descriptor** on the rootfs and re-walks each
#     resolved parent off it with O_NOFOLLOW (dirfd.descend_at), writing
#     every entry as (dir_fd, name). A component swapped for a symlink
#     after the resolve is refused instead of followed, which is the
#     difference between an archive-safe extractor and one that is also
#     safe against a process writing into the tree at the same time --
#     an image `install` is unpacking sits under CONTAINERS_DIR, which
#     on Termux is inside the $TERMUX_PREFIX bound read-write into every
#     non-isolated container. Files go in through open_new_at (O_EXCL),
#     so a hardlink left under a member's name is never written through
#     either, which is the one thing O_NOFOLLOW cannot refuse.
#   - Parent descriptors are cached one deep (_Parents). Members of a tar
#     arrive in tree order, so consecutive entries almost always share a
#     parent and the walk costs about one openat per member -- less than
#     the full path resolution the kernel did per call before.
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
    rootfs_fd: int,
    *,
    strip: int = 0,
    handle_whiteouts: bool = False,
) -> None:
    """Stream-extract *archive_path* into the directory *rootfs_fd* names.

    The rootfs is a **descriptor**, not a path: every member is written
    as (dir_fd, name) beneath it, so nothing below can be redirected by
    a component swapped since it was resolved, and the root itself is
    the inode the caller validated rather than a name it can be asked
    to resolve again. The caller owns the descriptor.

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
            raw_fh, os.path.getsize(archive_path), rootfs_fd,
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
    rootfs_fd: int,
    *,
    strip: int = 0,
    handle_whiteouts: bool = False,
    subject: str = "archive",
    expected_sha256: str = "",
) -> None:
    """Stream-extract the archive behind *fd* into the *rootfs_fd* tree.

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
            hashing, os.fstat(fd).st_size, rootfs_fd,
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


class _Parents:
    """One-deep cache of the descriptor a member's parent resolves to.

    Every entry is written as (dir_fd, name) off the directory the
    resolved components were re-walked to, and a tar lists its members
    in tree order, so consecutive entries nearly always share a parent.
    Holding the last one costs a single descriptor and saves the whole
    descent; a different parent closes it and walks again.

    The cache is keyed on the *components*, so a directory removed and
    remade between two members is never reused: any such member has a
    different parent (its own is one level up), which evicts the entry.
    """

    def __init__(self, root_fd: int):
        self._root_fd = root_fd
        self._key = None
        self._fd = None

    def get(self, parts, *, create: bool = True) -> int:
        """The descriptor for *parts* under the root. Raises OSError."""
        key = tuple(parts)
        if self._fd is not None and self._key == key:
            return self._fd
        self.close()
        fd = dirfd.descend_at(self._root_fd, key, create=create)
        self._key, self._fd = key, fd
        return fd

    def close(self) -> None:
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None
            self._key = None


def _extract_stream(raw_fh, total_size, rootfs_fd, *, strip,
                    handle_whiteouts) -> None:
    """The extraction proper, over an already-open binary stream."""
    deferred_links: list = []  # (dest, src) — copied after all regular files
    deferred_dirs: list = []   # (parts, mtime) — stamped after all writes

    parents = _Parents(rootfs_fd)
    try:
        counter = ByteCounter(raw_fh)
        with tarfile.open(fileobj=counter, mode="r|*") as tf:
            for member in tf:
                _process_member(
                    member, tf, rootfs_fd, parents,
                    strip=strip,
                    handle_whiteouts=handle_whiteouts,
                    deferred_links=deferred_links,
                    deferred_dirs=deferred_dirs,
                )
                draw_bytes_bar(counter.count, total_size)

        # All regular files written; now copy hard links. Both endpoints
        # are re-resolved here (not at defer time) so a symlink planted
        # by a later member can't redirect either the read source or the
        # write dest outside the rootfs — and the resolved components are
        # then walked off the rootfs descriptor, so neither end is opened
        # by a name that could have changed meaning since.
        for dest_parts, src_parts in deferred_links:
            _copy_hardlink(rootfs_fd, parents, dest_parts, src_parts)

        # Stamp directory mtimes last (writing files into a dir bumps it).
        for parts, mtime in reversed(deferred_dirs):
            try:
                parent_fd = parents.get(parts[:-1], create=False)
                os.utime(parts[-1], (mtime, mtime), dir_fd=parent_fd,
                         follow_symlinks=False)
            except OSError:
                pass
    finally:
        parents.close()

    clear_bar()


def _copy_hardlink(rootfs_fd, parents, dest_parts, src_parts) -> None:
    """Materialise one deferred hard-link member as a regular file.

    A hard link is stored as a copy of the backing file's content: the
    two endpoints of an archive's link may end up on different
    filesystems once restored, and a copy is what survives that. Mode
    and timestamps come across with it (dirfd.copy_file_at, which is
    shutil.copy2 expressed against descriptors).
    """
    src_resolved = safe_resolve_parts_at(rootfs_fd, src_parts)
    dest_resolved = safe_resolve_parts_at(rootfs_fd, dest_parts)
    if not src_resolved or not dest_resolved:
        return
    try:
        src_fd = dirfd.descend_at(rootfs_fd, src_resolved[:-1])
    except OSError:
        return
    try:
        try:
            st = dirfd.lstat_at(src_fd, src_resolved[-1])
        except OSError:
            return
        if not stat.S_ISREG(st.st_mode):
            # os.path.isfile() was the old test and it followed a link;
            # a hard-link member naming a symlink has no content of its
            # own to copy.
            return
        try:
            dst_fd = parents.get(dest_resolved[:-1])
        except OSError:
            return
        dirfd.unlink_quietly(dst_fd, dest_resolved[-1])
        try:
            dirfd.copy_file_at(src_fd, src_resolved[-1],
                               dst_fd, dest_resolved[-1], st)
        except OSError:
            pass
    finally:
        os.close(src_fd)


# ----- per-member dispatch -------------------------------------------------

def _process_member(member, tf, rootfs_fd, parents, *, strip,
                    handle_whiteouts, deferred_links, deferred_dirs):
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
        # paths './foo' as a matter of course and safe_resolve_parts
        # drops them on the way through.
        return

    # Resolve the destination's parent through any pre-existing symlink
    # components, clamping every hop inside the rootfs (see module
    # docstring). The final component is deliberately *not* followed so
    # we operate on the entry itself, never on whatever a same-named
    # symlink points at.
    parent_parts = safe_resolve_parts_at(rootfs_fd, rel_parts[:-1])
    if parent_parts is None:
        return
    name = rel_parts[-1]

    if handle_whiteouts and _is_whiteout(name):
        # No parent is created for a whiteout: it only ever removes, and
        # a directory that is not there has nothing in it to remove.
        try:
            parent_fd = parents.get(parent_parts, create=False)
        except OSError:
            return
        _apply_whiteout(parent_fd, name)
        return

    parent_fd = parents.get(parent_parts, create=True)

    if member.isdir():
        # A symlink already occupying this name would make the mkdir
        # (and the chmod/utime below) act on its target, so drop it
        # first — overlay semantics replace a symlink with a real dir.
        try:
            st = dirfd.lstat_at(parent_fd, name)
        except OSError:
            st = None
        if st is not None and stat.S_ISLNK(st.st_mode):
            dirfd.unlink_quietly(parent_fd, name)
        try:
            os.mkdir(name, 0o777, dir_fd=parent_fd)
        except FileExistsError:
            # os.makedirs(exist_ok=True) tolerated an existing directory
            # and nothing else; a plain file standing here still ends the
            # extraction rather than being written around.
            existing = None
            try:
                existing = dirfd.lstat_at(parent_fd, name)
            except OSError:
                pass
            if existing is None or not stat.S_ISDIR(existing.st_mode):
                raise
        dirfd.chmod_at(parent_fd, name,
                       stat.S_IMODE(member.mode) | stat.S_IRWXU)
        deferred_dirs.append((parent_parts + [name], member.mtime))

    elif member.issym():
        _write_symlink(parent_fd, name, member)

    elif member.islnk():
        _defer_hardlink(member, strip, rel_parts, deferred_links)

    elif member.isreg():
        _write_regular(parent_fd, name, member, tf)


def _is_whiteout(name: str) -> bool:
    """True when *name* is an OCI whiteout marker rather than an entry."""
    return name == ".wh..wh..opq" or name.startswith(".wh.")


def _apply_whiteout(parent_fd, basename) -> None:
    """Apply the OCI whiteout *basename* inside the directory parent_fd.

    Every removal is named as (dir_fd, entry) off the descriptor the
    resolved parent was walked to, so a whiteout cannot be aimed at
    anything the walk did not open itself.
    """
    if basename == ".wh..wh..opq":
        # Opaque whiteout: clear everything inside the parent dir.
        try:
            entries = dirfd.listdir_at(parent_fd)
        except OSError:
            return
        for entry in entries:
            dirfd.rmtree_at(parent_fd, entry, force=True)
        return
    if basename.startswith(".wh."):
        # Regular whiteout: delete the named sibling. What is deleted is
        # the part after the prefix, and it has to name a sibling: `.wh...`
        # slices to '..', which os.path.join left in the path and the
        # removal then rmtree'd — the parent's parent, which for a
        # whiteout at the top of a layer is one level *above* the
        # extraction root. A single such member emptied
        # containers/<name>/, manifest and rootfs together, during an
        # install of a crafted image. `.wh.` and `.wh..` slice to '' and
        # '.', which name the parent itself and cost it its contents.
        # None of the three names a sibling, so there is nothing for the
        # whiteout to delete; the member is still consumed, since an entry
        # called `.wh.*` is not one to write into the rootfs either.
        # Naming the entry as (dir_fd, target) makes this structural: a
        # component of a path is not something rmtree_at can be handed.
        target = basename[4:]
        if target not in ("", os.curdir, os.pardir):
            dirfd.rmtree_at(parent_fd, target, force=True)


def _write_symlink(parent_fd, name, member) -> None:
    """Write one symlink member as (parent_fd, name).

    Whatever holds the name first goes through rmtree_at, which unlinks
    a symlink rather than traversing it and empties a directory an
    earlier layer wrote however deep and however sealed the image made
    it — symlink(2) has no O_TRUNC and would only report EEXIST.
    """
    if dirfd.exists_at(parent_fd, name):
        dirfd.rmtree_at(parent_fd, name, force=True)
    try:
        os.symlink(member.linkname, name, dir_fd=parent_fd)
    except OSError:
        return
    try:
        os.utime(name, (member.mtime, member.mtime), dir_fd=parent_fd,
                 follow_symlinks=False)
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
    stored; both are resolved with safe_resolve_parts_at() at copy time
    so a symlink planted by a later member can't redirect the read
    source or the write dest out of the rootfs, and the answers are
    walked off the rootfs descriptor rather than opened by name.
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


def safe_resolve_parts_at(root_fd: int, parts):
    """safe_resolve_parts() against a root the caller has pinned.

    The same walk, with every lstat and readlink taken relative to
    *root_fd* instead of composed onto a root path, so the answer
    describes the tree below the descriptor the caller validated rather
    than below a name it would have to trust a second time. What comes
    back is still only where the entry *belongs* — the components have
    to be re-walked with dirfd.descend_at() before anything is written
    through them.
    """
    return safe_resolve_parts(None, parts, root_fd=root_fd)


def safe_resolve_parts(root, parts, *, root_fd: int = None):
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

    With *root_fd* the walk names each level relative to that descriptor
    and *root* is unused — safe_resolve_parts_at() is the spelling for
    that, and it is what a caller holding a pinned root wants. Without
    it the levels are composed onto *root*, which is right for a tree
    this process made itself.

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
        rel = os.path.join(*resolved, comp) if resolved else comp
        try:
            if root_fd is not None:
                st = os.lstat(rel, dir_fd=root_fd)
            else:
                st = os.lstat(os.path.join(root, rel))
        except OSError:
            # Doesn't exist yet (or unreadable) — safe to take as-is.
            resolved.append(comp)
            continue
        if stat.S_ISLNK(st.st_mode):
            link_budget -= 1
            if link_budget < 0:
                return None
            try:
                if root_fd is not None:
                    target = os.readlink(rel, dir_fd=root_fd)
                else:
                    target = os.readlink(os.path.join(root, rel))
            except OSError:
                return None
            tparts = target.split("/")
            if target.startswith("/"):
                resolved = []  # absolute target: re-root at *root*
            pending[:0] = tparts
        else:
            resolved.append(comp)
    return resolved


def _write_regular(parent_fd, name, member, tf) -> None:
    """Write one regular-file member as (parent_fd, name).

    open_new_at() creates a fresh inode with O_EXCL, unlinking whatever
    name was there first. That is what keeps the content inside the
    directory the walk opened even when the entry standing there is a
    *hardlink* to a file elsewhere — O_NOFOLLOW cannot tell one from an
    ordinary file, and an O_TRUNC write through it would land on the
    other inode. A directory in the way still ends the extraction, as
    open(dest, "wb") did on EISDIR.
    """
    fobj = tf.extractfile(member)
    if fobj is None:
        return
    try:
        fd, _st = dirfd.open_new_at(parent_fd, name,
                                    stat.S_IMODE(member.mode))
        try:
            with open(fd, "wb", closefd=False) as out:
                shutil.copyfileobj(fobj, out, 1 << 17)  # 128 KiB chunks
            # The mode open() created with is umask-masked, so it is set
            # again through the descriptor rather than by name.
            try:
                os.fchmod(fd, stat.S_IMODE(member.mode))
            except OSError:
                pass
        finally:
            os.close(fd)
        try:
            os.utime(name, (member.mtime, member.mtime), dir_fd=parent_fd,
                     follow_symlinks=False)
        except OSError:
            pass
    finally:
        fobj.close()
