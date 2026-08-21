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

# Architecture: Snapshot the rootfs before/after a RUN, diff the
# states, and emit an OCI-format gzipped tar layer that captures the
# delta (with whiteouts for deletions). The snapshot fingerprint for a
# regular file is (size, mtime_ns, mode, crc32): the lstat fields
# short-circuit obvious changes, and a streaming zlib.crc32 catches
# the same-(size, mtime) corner cases (touch -r, sub-second rewrites).
#
# The layer writer streams the tar payload through a 4-stage pipeline
# in a single pass:
#
#   tarfile  ->  [diff_id sha + progress]  ->  gzip  ->  [digest sha]  ->  out file
#
# diff_id is the sha256 of the *uncompressed* tar; digest is the
# sha256 of the gzipped output (= what the OCI manifest references).
# Both hashes plus the gzipped byte count fall out of the same pass,
# which avoids the previous three-pass (write tmp -> hash uncompressed
# -> gzip-stream -> hash compressed) dance and halves disk traffic.

import errno
import gzip
import hashlib
import os
import stat
import tarfile
import zlib

from proot_distro import dirfd
from proot_distro.atomic import atomic_write
from proot_distro.l2s import open_l2s_backing, resolve_l2s_target
from proot_distro.progress import (
    clear_bar, draw_bytes_bar, progress_active,
)


_CRC_CHUNK = 65536


def _file_crc32(dir_fd, name):
    """Return the zlib.crc32 of *name*'s content as an unsigned int.

    A 32-bit CRC is fast (C-implemented in zlib, ~GB/s) and good enough
    to distinguish content as long as we already trust the cheap (size,
    mtime) check to flag obvious modifications.

    Opened as (dir_fd, name) through open_regular_at, so the file whose
    content is read is the one the walk lstat'ed a moment ago, whatever
    a process left over from an earlier RUN does to the name in between.

    Returns 0xFFFFFFFF on read failure; that value collides with a
    legitimate CRC only with probability 1/2^32, and the file is going
    to be re-snapshotted on the next RUN anyway.
    """
    crc = 0
    try:
        fd, _st = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    except OSError:
        return 0xFFFFFFFF
    try:
        with open(fd, "rb", closefd=False) as fh:
            while True:
                chunk = fh.read(_CRC_CHUNK)
                if not chunk:
                    break
                crc = zlib.crc32(chunk, crc)
    except OSError:
        return 0xFFFFFFFF
    finally:
        os.close(fd)
    return crc & 0xFFFFFFFF


class MapSources:
    """The directories a file_map's "file" entries are read out of.

    An entry does not name a path to open. It names the *tree* it was
    found in -- the build context, another stage's rootfs, an image
    pulled for COPY --from, the build's own spool -- and the components
    below it, and both consumers (copy_step's materialiser and the
    packer below) re-walk those components from the root with
    O_NOFOLLOW before reading a byte.

    That is the difference between deciding where a source is and
    reading it. COPY/ADD enumerates a whole instruction first and
    consumes the map afterwards, twice, so between the lstat that
    recorded an entry and the read that packs it a component can be
    replaced with a symlink -- by a process an earlier RUN left running,
    which off Termux nothing kills, or simply by whoever else can write
    the tree. Resolving the name again then reads whatever it leads to
    now, and a layer is the worst place for a host file to turn up:
    `push` uploads it to a registry.

    One directory at a time is cached, which covers a whole directory's
    worth of entries: both consumers walk the map in sorted-arcname
    order and an arcname follows the layout of the source it came from.
    """

    def __init__(self):
        self._key = None
        self._fd = None

    def open(self, entry):
        """Open *entry*'s source as a regular file. Returns (fd, stat).

        Raises OSError when the walk refuses a component or the entry is
        no longer a regular file; the caller owns the descriptor. A
        "file" entry without a root and rel is a programming error, not
        a filesystem one, and raises KeyError rather than quietly
        reading a path.

        An entry may also carry `root_fd`, a descriptor its recorder
        holds on the tree for as long as the map lives. When it does,
        that is what the components are walked from: the root itself is
        a name otherwise, and for a stage rootfs it is a name inside the
        build's scratch tree, which is guest-reachable on Termux.
        """
        root = entry["root"]
        root_fd = entry.get("root_fd")
        rel = tuple(entry["rel"])
        if not rel:
            raise OSError(errno.EINVAL, "source entry names no file", root)
        key = (root, root_fd, rel[:-1])
        if key != self._key:
            self.close()
            # A pinned root is descended from directly: going back to its
            # path would re-resolve the very name the pin exists to
            # settle -- a stage rootfs, for COPY --from=<stage>.
            if root_fd is not None:
                fd = dirfd.descend_at(root_fd, rel[:-1])
            else:
                opened = dirfd.opendir(root)
                try:
                    fd = dirfd.descend_at(opened, rel[:-1])
                finally:
                    os.close(opened)
            self._fd, self._key = fd, key
        return dirfd.open_regular_at(self._fd, rel[-1], os.O_RDONLY)

    def close(self):
        if self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd, self._key = None, None

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        self.close()
        return False


class _ParentFds:
    """Directory descriptors for the parents of the entries being packed.

    Every rel handed to _add_entry came off snapshot()'s own walk, so its
    parents were real directories then. Between then and the pack a
    process an earlier RUN left running can replace one with a symlink --
    off Termux nothing kills such a process, since --kill-on-exit is a
    Termux-only extension -- and naming the entry then reads through it.
    A layer is the worst place for that: `push` uploads it to a registry.

    So each parent is re-walked from the rootfs descriptor with
    O_NOFOLLOW and the entry is addressed as (dir_fd, name). The rels
    arrive sorted, so caching the last parent covers a whole directory's
    worth of entries and the walk costs about one openat apiece.

    *rootfs_fd* is the rootfs when the caller has pinned it, and a caller
    that has one must pass it: the rootfs is the one directory this walk
    cannot vouch for itself, and a build stage's is a name inside the
    build's scratch tree -- 0700, but reachable by anything running as
    the invoking user, which on Termux is every RUN step's guest.
    """

    def __init__(self, rootfs, *, rootfs_fd=None):
        self._root_fd = (dirfd.reopen(rootfs_fd) if rootfs_fd is not None
                         else dirfd.opendir(rootfs))
        self._rel = None
        self._fd = None
        self._owned = False

    def open(self, parent_rel):
        """Return a descriptor for *parent_rel* under the rootfs, or None."""
        if self._fd is not None and parent_rel == self._rel:
            return self._fd
        self._release()
        self._rel = parent_rel
        if not parent_rel:
            self._fd, self._owned = self._root_fd, False
            return self._fd
        fd, owned = self._root_fd, False
        for comp in parent_rel.split("/"):
            try:
                nxt = dirfd.opendir_at(fd, comp)
            except OSError:
                if owned:
                    os.close(fd)
                self._fd, self._owned = None, False
                return None
            if owned:
                os.close(fd)
            fd, owned = nxt, True
        self._fd, self._owned = fd, owned
        return fd

    def _release(self):
        if self._owned and self._fd is not None:
            try:
                os.close(self._fd)
            except OSError:
                pass
        self._fd, self._owned = None, False

    def close(self):
        self._release()
        try:
            os.close(self._root_fd)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Snapshot / diff
# ---------------------------------------------------------------------------

def snapshot(rootfs, *, rootfs_fd=None):
    """Return {rel_path: fingerprint_tuple} for every entry under rootfs.

    Tuple kinds:
        ("dir", mode)
        ("symlink", target)
        ("file", size, mtime_ns, mode, crc32)
    Block/char devices, FIFOs, sockets, etc. are skipped silently.

    Comparison semantics (via tuple equality during `diff_snapshots`):
    Python's tuple `==` short-circuits at the first differing field,
    so if `size` or `mtime_ns` between the before- and after-snapshot
    entries already differ, the file is flagged modified without
    consulting CRC32 at all. CRC32 is the tie-breaker for the corner
    cases the (size, mtime) pair can't catch on its own — namely
    `touch -r`-style mtime preservation and sub-second double-writes.

    The `.l2s/` directory at the rootfs root is skipped entirely.
    It is proot's link2symlink backing store — internal to the
    build's tmp rootfs and not part of the user-visible filesystem.
    Symlinks pointing into it are resolved to file content at
    layer-write time (see `_add_entry`).

    The walk carries directory descriptors rather than paths: os.scandir
    on a name descends whatever it resolves to now, and the CRC then
    opened that name a second time, so a process an earlier RUN left
    running could have a host file's content decide a fingerprint. How
    many it holds is bounded (dirfd.Levels), because the rootfs's depth
    is the image's business: one descriptor per level ran the process out
    of them partway down, and every entry below that point was left out
    of the snapshot -- and so out of the layer -- without a word.

    *rootfs_fd* is the rootfs when the caller has pinned it, which the
    build's RUN step has: its two snapshots straddle the step, so the
    name they used to resolve was one the step itself had had the run of.
    """
    state = {}
    try:
        root_fd = (dirfd.reopen(rootfs_fd) if rootfs_fd is not None
                   else dirfd.opendir(rootfs))
    except OSError:
        return state

    # Frame layout: [fd, None, pending names, rel prefix, owned].
    stack = [[root_fd, None, None, "", True]]
    levels = dirfd.Levels(stack)
    try:
        while stack:
            frame = stack[-1]
            fd, _, pending, rel_prefix, owned = frame
            if pending is None:
                try:
                    pending = frame[2] = dirfd.listdir_at(fd)
                except OSError:
                    pending = frame[2] = []
            if not pending:
                levels.pop()
                if owned:
                    os.close(fd)
                continue

            name = pending.pop()
            rel = rel_prefix + name if rel_prefix else name
            try:
                st = dirfd.lstat_at(fd, name)
            except OSError:
                continue
            mode = st.st_mode
            if stat.S_ISLNK(mode):
                try:
                    state[rel] = ("symlink", os.readlink(name, dir_fd=fd))
                except OSError:
                    pass
            elif stat.S_ISDIR(mode):
                if rel_prefix == "" and name == ".l2s":
                    continue
                state[rel] = ("dir", stat.S_IMODE(mode))
                try:
                    sub = dirfd.opendir_at(fd, name)
                except OSError:
                    continue
                levels.push([sub, None, None, rel + "/", True])
            elif stat.S_ISREG(mode):
                state[rel] = (
                    "file",
                    st.st_size,
                    st.st_mtime_ns,
                    stat.S_IMODE(mode),
                    _file_crc32(fd, name),
                )
            # Other types intentionally skipped.
    except BaseException:
        dirfd.close_frames(stack)
        raise
    return state


def diff_snapshots(before, after):
    """Return (added, modified, deleted), each a sorted list of rel paths."""
    added = []
    modified = []
    for k, v in after.items():
        if k not in before:
            added.append(k)
        elif before[k] != v:
            modified.append(k)
    deleted = [k for k in before if k not in after]
    return sorted(added), sorted(modified), sorted(deleted)


def _whiteout_paths(deleted, surviving_dirs):
    """Translate a list of deleted rel paths into OCI whiteout entries."""
    arcnames = []
    for rel in sorted(set(deleted)):
        parent = os.path.dirname(rel)
        basename = os.path.basename(rel)
        if parent:
            arcnames.append(parent + "/.wh." + basename)
        else:
            arcnames.append(".wh." + basename)
    for parent in sorted(surviving_dirs):
        if parent:
            arcnames.append(parent + "/.wh..wh..opq")
        else:
            arcnames.append(".wh..wh..opq")
    return arcnames


# ---------------------------------------------------------------------------
# Streaming layer-tar writer + progress bar
# ---------------------------------------------------------------------------

class _ProgressHashTee:
    """File-like wrapper. write() forwards bytes to `fh`, updates `hasher`,
    accumulates a byte counter, and triggers an optional progress
    callback throttled to once per 256 KiB or more.
    """

    def __init__(self, fh, hasher, on_progress=None):
        self._fh = fh
        self._hasher = hasher
        self._on_progress = on_progress
        self.count = 0
        self._last_shown = 0

    def write(self, data):
        if isinstance(data, memoryview):
            data = bytes(data)
        self._hasher.update(data)
        self.count += len(data)
        if self._on_progress is not None:
            if self.count - self._last_shown >= 262144:
                self._last_shown = self.count
                self._on_progress(self.count)
        return self._fh.write(data)

    def flush(self):
        return self._fh.flush()


def _make_progress_callback(total_size):
    """Return a (callback, finaliser) pair for a stderr progress bar."""
    if not progress_active():
        return (lambda _done: None), (lambda: None)

    def _show(done):
        draw_bytes_bar(done, total_size, noun="packed")

    return _show, clear_bar


def _pack_stream(out_path, total_uncompressed, populate):
    """Run `populate(tf)` against a tarfile.TarFile that streams its
    output through a hash+gzip+hash pipeline into `out_path`.

    `total_uncompressed` is the expected number of tar payload bytes
    (sum of regular-file sizes) used only for the progress bar.
    Headers and padding add a small constant overhead beyond this.

    Returns (digest, gzipped_size, diff_id).

    Written through atomic.atomic_write(), which is what every other
    writer inside the state tree uses: the layer lands in the build's
    scratch root, and `open(out_path + ".tmp", "wb")` resolved that name
    -- `RUNTIME_DIR/build-tmp/<run>/layer-i-j.tar.gz.tmp`, every
    component of it predictable once the run directory is listed -- so a
    symlink left under it had the layer's bytes written into whatever it
    named, and the os.replace() that followed moved the *link* on to be
    published into the layer cache. atomic_write walks down to the
    directory with O_NOFOLLOW, creates the temporary O_EXCL off the
    descriptor it validated, and never names it again.
    """
    digest_h = hashlib.sha256()
    diff_id_h = hashlib.sha256()
    show, clear = _make_progress_callback(total_uncompressed)

    gz = None
    tf = None
    digest_tee = None
    try:
        with atomic_write(out_path, "wb") as out_fh:
            digest_tee = _ProgressHashTee(out_fh, digest_h)
            gz = gzip.GzipFile(fileobj=digest_tee, mode="wb", mtime=0)
            diff_id_tee = _ProgressHashTee(gz, diff_id_h, on_progress=show)
            tf = tarfile.open(fileobj=diff_id_tee, mode="w|")
            populate(tf)
            tf.close()
            tf = None
            gz.close()
            gz = None
            out_fh.flush()
            clear()
    except BaseException:
        clear()
        for handle in (tf, gz):
            if handle is not None:
                try:
                    handle.close()
                except OSError:
                    pass
        raise

    return (
        "sha256:" + digest_h.hexdigest(),
        digest_tee.count,
        "sha256:" + diff_id_h.hexdigest(),
    )


# ---------------------------------------------------------------------------
# Public layer writers
# ---------------------------------------------------------------------------

def write_layer_tar(rootfs, paths_to_pack, deleted, out_path,
                    opaque_dirs=(), *, rootfs_fd=None):
    """Write a gzipped OCI layer to `out_path`.

    paths_to_pack: rel paths whose current state in `rootfs` should be
                   packed (the union of added + modified).
    deleted:       rel paths that disappeared since the snapshot.
    opaque_dirs:   rel paths of directories that survived but had all
                   children removed (emit `.wh..wh..opq` inside them).
    rootfs_fd:     the rootfs as the caller pinned it. A caller that has
                   one must pass it — everything below is addressed as
                   (dir_fd, name) already, but the walk has to start
                   somewhere, and `<scratch>/stage-N/rootfs` is a name
                   whatever a RUN step left running can re-point.

    Returns (digest, size, diff_id) where digest is "sha256:<hex>" of
    the gzipped bytes, size is the gzipped byte count, and diff_id is
    "sha256:<hex>" of the uncompressed tar bytes.
    """
    sorted_paths = sorted(paths_to_pack)
    try:
        parents = _ParentFds(rootfs, rootfs_fd=rootfs_fd)
    except OSError:
        parents = None

    # The progress denominator comes off the same descriptors the pack
    # reads through: os.lstat(os.path.join(rootfs, rel)) resolved every
    # component of every entry by name all over again.
    total = 0
    if parents is not None:
        for rel in sorted_paths:
            parent_rel, _, name = rel.rpartition("/")
            dir_fd = parents.open(parent_rel)
            if dir_fd is None:
                continue
            try:
                st = dirfd.lstat_at(dir_fd, name)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode):
                total += st.st_size

    def _populate(tf):
        for rel in sorted_paths:
            if parents is not None:
                _add_entry(tf, parents, rootfs, rel, rootfs_fd=rootfs_fd)
        for wh in _whiteout_paths(deleted, opaque_dirs):
            _add_whiteout(tf, wh)

    try:
        return _pack_stream(out_path, total, _populate)
    finally:
        if parents is not None:
            parents.close()


def layer_path_parts(arcname):
    """The components *arcname* names inside the image, or None if it escapes.

    One rule for the two halves of a COPY/ADD, which used to filter
    separately: only the materialiser dropped a name containing "..", so
    `COPY x /../foo` wrote nothing to the rootfs and packed "../foo" into
    the layer -- plus a synthesised ".." directory entry above it, since
    the parent loop below walks whatever components it is given.

    Nothing this program extracts would apply either one; tar_extract
    drops them on the way back in. But a layer is the artefact that
    leaves the machine, and once `push` has uploaded it, what ".." means
    is decided by whatever loads it next. So the packer holds its input
    to the same rule as the tree, rather than trusting a caller to have
    checked.
    """
    parts = [p for p in arcname.split("/") if p not in ("", os.curdir)]
    if not parts or os.pardir in parts:
        return None
    return parts


def write_files_layer(file_map, out_path):
    """Pack a {arcname → entry} mapping into a gzipped OCI layer.

    Every entry is a dict describing what to write: a "dir", a
    "symlink", or a "file" naming the tree its bytes come from (see
    MapSources). The progress denominator is the size each "file" entry
    recorded when it was enumerated, so the pack stats nothing by name
    on the way in either.
    """
    sorted_items = sorted(file_map.items())

    # Pre-computed for the progress bar from what the enumeration saw.
    total = sum(
        entry.get("size", 0)
        for _arcname, entry in sorted_items
        if entry.get("kind") == "file"
    )

    def _populate(tf):
        # Synthesise parent directory entries so the layer applies
        # cleanly even when intermediate dirs were not COPY'd. Both loops
        # go through layer_path_parts, so a name that escapes the image
        # root contributes neither an entry nor an ancestor.
        seen_dirs = set()
        for arcname, _ in sorted_items:
            parts = layer_path_parts(arcname)
            if parts is None:
                continue
            for k in range(1, len(parts)):
                dpath = "/".join(parts[:k])
                if dpath not in seen_dirs:
                    seen_dirs.add(dpath)
                    dinfo = tarfile.TarInfo(dpath)
                    dinfo.type = tarfile.DIRTYPE
                    dinfo.mode = 0o755
                    dinfo.mtime = 0
                    tf.addfile(dinfo)
        with MapSources() as sources:
            for arcname, entry in sorted_items:
                if layer_path_parts(arcname) is None:
                    continue
                _add_file_map_entry(tf, arcname, entry, sources)

    return _pack_stream(out_path, total, _populate)


# ---------------------------------------------------------------------------
# Per-entry tar emitters
# ---------------------------------------------------------------------------

def _add_entry(tf, parents, rootfs, rel, *, rootfs_fd=None):
    """Add the on-disk entry at <rootfs>/<rel> to the tar by arcname=rel.

    *parents* supplies the descriptor of the entry's parent directory, so
    every one of the calls below names the entry relative to a directory
    the walk itself opened — see _ParentFds. The joined path is still
    needed for the l2s chain, whose containment is a question about paths
    (resolve_l2s_target realpaths the whole chain). The *read* that
    follows goes through open_l2s_backing, which re-walks the answer from
    *rootfs_fd* when there is one — so however the path resolves, the
    bytes can only come from inside the tree the caller pinned.
    """
    parent_rel, _, name = rel.rpartition("/")
    dir_fd = parents.open(parent_rel)
    if dir_fd is None:
        return
    full = os.path.join(rootfs, rel)
    try:
        st = dirfd.lstat_at(dir_fd, name)
    except OSError:
        return

    tinfo = tarfile.TarInfo(rel)
    tinfo.uid = 0
    tinfo.gid = 0
    tinfo.uname = ""
    tinfo.gname = ""
    tinfo.mtime = int(st.st_mtime)
    tinfo.mode = stat.S_IMODE(st.st_mode)

    if stat.S_ISLNK(st.st_mode):
        try:
            target = os.readlink(name, dir_fd=dir_fd)
        except OSError:
            return

        # proot's --link2symlink extension turns hard-link calls into
        # symlinks pointing at an intermediate file (in <rootfs>/.l2s/
        # when PROOT_L2S_DIR is set, alongside the original otherwise).
        # Either way the targets are absolute paths into the build's
        # tmp rootfs and would dangle once the image is applied
        # elsewhere; resolve_l2s_target spots the chain by basename
        # prefix. Pack the backing file's content as a regular file so
        # the layer is self-contained.
        l2s_path = resolve_l2s_target(full, target, rootfs)
        if l2s_path is not None:
            # Through a descriptor rather than the name, so a component
            # re-pointed after the resolve fails instead of being followed
            # (see l2s.open_l2s_backing). A layer is the worse place for
            # that to go unchecked: `push` uploads it to a registry.
            opened = open_l2s_backing(rootfs, l2s_path, rootfs_fd=rootfs_fd)
            if opened is not None:
                cfd, cst = opened
                try:
                    tinfo.type = tarfile.REGTYPE
                    tinfo.size = cst.st_size
                    tinfo.mode = stat.S_IMODE(cst.st_mode)
                    tinfo.mtime = int(cst.st_mtime)
                    try:
                        tf.addfile(tinfo, open(cfd, "rb", closefd=False))
                    except OSError:
                        pass
                finally:
                    os.close(cfd)
                return

        try:
            tinfo.type = tarfile.SYMTYPE
            tinfo.linkname = target
            tinfo.size = 0
            tf.addfile(tinfo)
        except OSError:
            pass
    elif stat.S_ISDIR(st.st_mode):
        tinfo.type = tarfile.DIRTYPE
        tinfo.size = 0
        tf.addfile(tinfo)
    elif stat.S_ISREG(st.st_mode):
        # The size comes off the fstat of the descriptor that is about to
        # be read, not off the earlier lstat of the name: those are two
        # different files the moment anything replaces the entry, and
        # tarfile writes exactly tinfo.size bytes from what it is handed.
        try:
            fd, fst = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
        except OSError:
            return
        try:
            tinfo.type = tarfile.REGTYPE
            tinfo.size = fst.st_size
            try:
                tf.addfile(tinfo, open(fd, "rb", closefd=False))
            except OSError:
                pass
        finally:
            os.close(fd)
    # Other types intentionally skipped (devices, FIFOs).


def _add_whiteout(tf, arcname):
    tinfo = tarfile.TarInfo(arcname)
    tinfo.type = tarfile.REGTYPE
    tinfo.size = 0
    tinfo.mode = 0o644
    tinfo.mtime = 0
    tinfo.uid = 0
    tinfo.gid = 0
    tinfo.uname = ""
    tinfo.gname = ""
    tf.addfile(tinfo)


def _add_file_map_entry(tf, arcname, entry, sources):
    """Add one file_map entry to the tar under *arcname*.

    A "file" entry's bytes come out of the descriptor *sources* opens
    for it, and its size off that descriptor's own fstat -- never off an
    lstat of a name that is opened again afterwards. Its mode, uid and
    gid come from the entry (that is how COPY --chown and --chmod reach
    the layer) while its timestamp comes from the file, which is where
    ADD parks a spooled member's mtime.
    """
    kind = entry.get("kind")
    if kind == "symlink":
        tinfo = tarfile.TarInfo(arcname)
        tinfo.type = tarfile.SYMTYPE
        tinfo.linkname = entry["target"]
        tinfo.size = 0
        tinfo.mode = entry.get("mode", 0o777)
        tinfo.mtime = entry.get("mtime", 0)
        tinfo.uid = entry.get("uid", 0)
        tinfo.gid = entry.get("gid", 0)
        tf.addfile(tinfo)
        return
    if kind == "dir":
        tinfo = tarfile.TarInfo(arcname)
        tinfo.type = tarfile.DIRTYPE
        tinfo.mode = entry.get("mode", 0o755)
        tinfo.mtime = entry.get("mtime", 0)
        tinfo.uid = entry.get("uid", 0)
        tinfo.gid = entry.get("gid", 0)
        tf.addfile(tinfo)
        return
    # There is deliberately no in-memory "content" kind: a file_map
    # covers a whole instruction, so every entry's bytes would be live
    # at once. Content that is not already a file is spooled to one
    # (see build_engine.copy_step._spool_entry).
    if kind != "file":
        return

    try:
        fd, fst = sources.open(entry)
    except OSError:
        return
    try:
        tinfo = tarfile.TarInfo(arcname)
        tinfo.type = tarfile.REGTYPE
        tinfo.size = fst.st_size
        tinfo.mode = entry.get("mode", stat.S_IMODE(fst.st_mode))
        tinfo.mtime = int(fst.st_mtime)
        tinfo.uid = entry.get("uid", 0)
        tinfo.gid = entry.get("gid", 0)
        tf.addfile(tinfo, open(fd, "rb", closefd=False))
    finally:
        os.close(fd)
