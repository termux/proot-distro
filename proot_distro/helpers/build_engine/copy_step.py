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

# Architecture: The COPY and ADD handlers. Each instruction produces
# exactly one layer assembled from a file_map (arcname -> entry dict)
# that is also materialised onto the rootfs at the same time. Sources
# may be the build context, another stage (--from=<stage>), an external
# image (--from=<image:tag>), or — for ADD — an HTTP/HTTPS URL.
#
# No entry ever holds content in memory. A file_map covers a whole
# instruction at once, so ADD used to read an entire URL response — and
# then every regular member of an auto-extracted archive, all of them live
# at the same time — into RAM, and a single instruction could take the
# build process out. Content that does not already exist as a file is
# spooled into engine.tmp_root and referenced by path, which is where it
# was headed anyway: the instruction both materialises it into the rootfs
# and packs it into a layer, and tmp_root is removed when the build ends.
#
# No entry names a path to read, either. A source is resolved beneath the
# tree it came from and recorded as (root, components); every read walks
# those components back down from the root with O_NOFOLLOW — see
# _SourceTree here and layer_diff.MapSources on the consuming side. The
# lexical prefix check that came before decided containment on the
# spelling of a path, which says nothing about the symlinks in it: a
# build context holding `escape -> /` let `COPY escape/etc/passwd
# /leaked` read the host's file, and a source image shipping the same
# link let `COPY --from` do it without the context containing anything
# at all. Both ended up in the layer `push` uploads.

import hashlib
import os
import re
import shlex
import shutil
import stat
import tarfile
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.message import log_info
from proot_distro.helpers.build_engine.dockerignore import (
    is_ignored, simple_glob,
)
from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.build_engine.parsing import (
    TAR_HEADER_BYTES, is_tar_header, looks_like_url,
)
from proot_distro.helpers.build_engine.users import resolve_chown
from proot_distro.helpers.docker import (
    AuthStrippingRedirectHandler, layer_cache_path, pull_image,
)
from proot_distro.helpers.layer_diff import (
    MapSources, layer_path_parts, write_files_layer,
)
from proot_distro.helpers.tar_extract import safe_resolve_parts
from proot_distro import dirfd
from proot_distro.atomic import publish_file


# Chunk size for spooling, the same one tar_extract streams with.
_SPOOL_CHUNK = 1 << 17


class _SourceTree:
    """The tree one COPY/ADD reads its sources out of.

    The build context, another stage's rootfs, or an image pulled for
    COPY --from — trees this program did not write, whose symlinks are
    therefore whoever wrote them's choice.

    `resolve()` answers where a source spec lands with tar_extract's
    clamped walk: existing symlink components are followed, but an
    absolute target re-anchors at the root and `..` can never climb out
    of it. That is both the confinement and the meaning a path has
    inside an image, where the guest's `/` *is* the rootfs, so an
    absolute link an image legitimately ships (`/usr/bin/python ->
    /usr/local/bin/python`) still resolves to the right file. The final
    component is deliberately left unresolved: COPY copies a symlink as
    a symlink rather than reading through it.

    Nothing here hands a path back to open. Resolving decides each
    component by name, so a component swapped afterwards would be
    followed by whatever acted on the answer; every descriptor this
    class returns is walked down from the root with O_NOFOLLOW instead,
    and the entries recorded in a file_map carry (root, components) so
    the reads that come later can do the same.
    """

    def __init__(self, root):
        self.root = os.path.abspath(root)

    def resolve(self, parts):
        """Where *parts* lands beneath the root, as components, or None.

        None means a symlink loop or chain long enough to look like one.
        """
        parts = [p for p in parts if p not in ("", os.curdir)]
        if not parts:
            return []
        resolved = safe_resolve_parts(self.root, parts[:-1])
        if resolved is None:
            return None
        return resolved + [parts[-1]]

    def opendir(self, parts):
        """A descriptor on the directory *parts* names. Raises OSError."""
        root_fd = dirfd.opendir(self.root)
        try:
            return dirfd.descend_at(root_fd, parts)
        finally:
            os.close(root_fd)

    def lstat(self, parts):
        """What *parts* names, without following it. stat, or None."""
        if not parts:
            try:
                return os.stat(self.root)
            except OSError:
                return None
        try:
            fd = self.opendir(parts[:-1])
        except OSError:
            return None
        try:
            return dirfd.lstat_at(fd, parts[-1])
        except OSError:
            return None
        finally:
            os.close(fd)

    def open_file(self, parts):
        """Open *parts* as a regular file. (fd, stat); raises OSError."""
        fd = self.opendir(parts[:-1])
        try:
            return dirfd.open_regular_at(fd, parts[-1], os.O_RDONLY)
        finally:
            os.close(fd)


def _spec_parts(src):
    """The components of a COPY/ADD source spec, or None for a `..` in it.

    A leading '/' is dropped: Docker reads both spellings as relative to
    the source tree's own root. A `..` written in the spec is refused
    rather than clamped — the same rule the [name:]path resolver applies
    to a container path, and the same answer Docker gives for a source
    outside the build context.
    """
    parts = [p for p in src.lstrip("/").split("/") if p not in ("", os.curdir)]
    if os.pardir in parts:
        return None
    return parts


def _rel_name(parts):
    """The '/'-joined form of *parts*, as .dockerignore matches names."""
    return "/".join(parts) if parts else os.curdir


def _spool_dir(engine):
    """The build's scratch directory for ADD content, created on demand."""
    path = os.path.join(engine.tmp_root, "add-spool")
    os.makedirs(path, exist_ok=True)
    return path


def _spool_stream(fobj, spool):
    """Copy *fobj* into a fresh file under *spool*; return its path."""
    fd, path = tempfile.mkstemp(dir=spool, prefix="add-")
    try:
        with os.fdopen(fd, "wb") as out:
            shutil.copyfileobj(fobj, out, _SPOOL_CHUNK)
    except BaseException:
        try:
            os.unlink(path)
        except OSError:
            pass
        raise
    return path


def _file_entry(file_map, arcname, root, parts, mode, uid, gid, mtime, size):
    """Record a regular file in *file_map* as (root, components).

    `src` is the joined form, for a message that has to name the source;
    nothing reads through it. The bytes come from a walk of `rel` down
    from `root` (layer_diff.MapSources), and `size` is what the
    enumeration measured, which is all the progress bar's denominator
    needs — the pack sizes each file off the descriptor it reads.
    """
    file_map[arcname] = {
        "kind": "file",
        "root": root, "rel": tuple(parts),
        "src": os.path.join(root, *parts),
        "mode": mode, "uid": uid, "gid": gid, "mtime": mtime, "size": size,
    }


def _spool_entry(file_map, arcname, path, mode, uid, gid, mtime):
    """Record a spooled file in *file_map* as an ordinary file entry.

    The timestamp goes on the spool file itself because that is where both
    consumers read it from: layer_diff's "file" kind takes an entry's mode,
    uid and gid from the dict but its mtime from the file on disk. The
    value came out of an archive header or off the clock, so it can be any
    number at all -- os.utime() raises OverflowError, not OSError, on one
    the platform cannot store.
    """
    try:
        os.utime(path, (mtime, mtime))
    except (OSError, OverflowError, ValueError):
        pass
    try:
        size = os.stat(path).st_size
    except OSError:
        size = 0
    root, name = os.path.split(path)
    _file_entry(file_map, arcname, root, [name], mode, uid, gid, mtime, size)


def do_copy(engine, instr):
    """COPY [--from=X] [--chown] [--chmod] SRC DEST: pack files into a layer."""
    _do_copy_or_add(engine, instr, allow_url=False, auto_extract=False)


def do_add(engine, instr):
    """ADD: like COPY but accepts URL sources and auto-extracts tarballs."""
    _do_copy_or_add(engine, instr, allow_url=True, auto_extract=True)


def _do_copy_or_add(engine, instr, allow_url, auto_extract):
    stage = engine.current
    flags = instr.get("flags") or {}

    if instr["exec_form"]:
        tokens = list(instr["value"])
    else:
        tokens = shlex.split(str(instr["value"]))
    if len(tokens) < 2:
        raise BuildError(
            f"{instr['name']} requires at least one source and a "
            f"destination at line {instr['lineno']}."
        )

    sources = tokens[:-1]
    dest = tokens[-1]

    # Reject BuildKit-only flags loudly.
    for k in flags:
        if k in ("link", "parents"):
            raise BuildError(
                f"{instr['name']} --{k} is a BuildKit-only flag and is "
                f"not supported (line {instr['lineno']})."
            )

    chown = flags.get("chown")
    chmod = flags.get("chmod")
    from_stage = flags.get("from")
    from_rootfs = None
    if from_stage:
        ref_stage = engine.stages.get(from_stage)
        if ref_stage is None:
            from_rootfs = _pull_throwaway_image(engine, from_stage)
        else:
            from_rootfs = ref_stage.rootfs_dir

    resolved = []
    if from_rootfs is None:
        for src in sources:
            if allow_url and looks_like_url(src):
                resolved.append(("url", src))
            else:
                resolved.append(("ctx", src))
    else:
        for src in sources:
            resolved.append(("rootfs", src))

    is_dir_dest = dest.endswith("/") or len(sources) > 1
    # Normalised whether or not it is absolute. ".." inside an image is
    # resolved against the guest's "/", so `/../foo` is `/foo` -- which is
    # what Docker makes of it. Only the relative branch used to be
    # normalised, so an absolute dest carried its ".." all the way to the
    # arcname, where the tree dropped the entry and the layer kept it.
    # The trailing slash is restored because it is read again further
    # down (_copy_url, _dest_arcname, _add_directory_tree) and normpath
    # strips it; without that, normalising here would quietly change what
    # `COPY x /opt/app/` means.
    trailing = dest.endswith("/")
    dest = os.path.normpath(os.path.join(stage.workdir or "/", dest))
    if trailing and not dest.endswith("/"):
        dest += "/"

    uid, gid = resolve_chown(stage.rootfs_dir, chown) if chown else (0, 0)
    mode_override = (
        int(chmod, 8) if chmod and re.match(r"^[0-7]+$", chmod) else None
    )

    file_map = {}
    spool = _spool_dir(engine) if allow_url or auto_extract else None
    for kind, src in resolved:
        if kind == "url":
            _copy_url(src, dest, file_map, uid, gid, mode_override, spool)
        elif kind == "ctx":
            _copy_from_context(
                engine, src, dest, is_dir_dest, file_map,
                uid, gid, mode_override, auto_extract, spool,
            )
        elif kind == "rootfs":
            _copy_from_rootfs(
                from_rootfs, src, dest, is_dir_dest, file_map,
                uid, gid, mode_override,
            )

    if not file_map:
        return

    _materialise_files(stage.rootfs_dir, file_map)

    tmp_layer_path = os.path.join(
        engine.tmp_root,
        f"layer-{stage.index}-{len(stage.layers)}.tar.gz",
    )
    digest, size, diff_id = write_files_layer(file_map, tmp_layer_path)
    # See run_step: the layer cache is walked down to, not named.
    publish_file(tmp_layer_path, layer_cache_path(digest))
    stage.layers.append(
        {"digest": digest, "size": size, "diff_id": diff_id}
    )
    stage.parent_layer_digest = digest


def _pull_throwaway_image(engine, image_ref):
    """Pull an external image into a tmp rootfs for COPY --from."""
    slot = hashlib.sha256(image_ref.encode()).hexdigest()[:16]
    rootfs = os.path.join(engine.tmp_root, "copyfrom-" + slot)
    if os.path.isdir(rootfs) and os.listdir(rootfs):
        return rootfs
    os.makedirs(rootfs, exist_ok=True)
    if not engine.quiet:
        log_info(f"COPY --from='{image_ref}': fetching external image...")
    try:
        rootfs_fd = dirfd.opendir(rootfs)
    except OSError as exc:
        raise BuildError(f"COPY --from={image_ref}: {exc}") from exc
    try:
        pull_image(image_ref, rootfs_fd, engine.target_arch_pd)
    except RuntimeError as exc:
        raise BuildError(f"COPY --from={image_ref}: {exc}") from exc
    finally:
        os.close(rootfs_fd)
    return rootfs


def _copy_from_context(engine, src, dest, is_dir_dest, file_map,
                       uid, gid, mode_override, auto_extract, spool=None):
    """COPY/ADD from the build context, confined to it.

    A source that resolves outside the context does not exist as far as
    this is concerned: `..` in the spec is refused outright, and a
    symlink leading out of the context re-anchors at its root, so what
    was `escape/secret` with `escape -> /` becomes plain `secret` and is
    reported missing if the context holds no such file. That is what the
    daemon makes of a context symlink too — it only ever sees the
    unpacked context, never the host tree the link named.
    """
    tree = _SourceTree(engine.build_dir)
    raw = _spec_parts(src)
    if raw is None:
        raise BuildError(
            f"COPY source '{src}' escapes the build context."
        )
    parts = tree.resolve(raw)
    st = tree.lstat(parts) if parts is not None else None
    if st is None:
        # A wildcard, or a name that is not there. glob() answers on the
        # spelling of a path the same way the old containment check did,
        # so every match is put through the walk as well and one that
        # only exists outside the context counts for nothing: with no
        # match left the source is not in the context, which is what the
        # user is told rather than the instruction quietly copying
        # nothing.
        matches = sorted(simple_glob(engine.build_dir, src.lstrip("/")))
        matches = [m for m in matches if not is_ignored(m, engine.ignore_patterns)]
        found = []
        for m in matches:
            m_raw = _spec_parts(m)
            m_parts = tree.resolve(m_raw) if m_raw is not None else None
            m_st = tree.lstat(m_parts) if m_parts is not None else None
            if m_st is not None:
                found.append((m_parts, m_st))
        if not found:
            raise BuildError(
                f"COPY/ADD source '{src}' not found in build context."
            )
        for m_parts, m_st in found:
            _add_to_file_map(
                tree, m_parts, m_st, dest, is_dir_dest=True, file_map=file_map,
                uid=uid, gid=gid, mode_override=mode_override,
                auto_extract=auto_extract, src_rel=_rel_name(m_parts),
                ignore_patterns=engine.ignore_patterns, spool=spool,
            )
        return
    rel = _rel_name(parts)
    if is_ignored(rel, engine.ignore_patterns):
        return
    _add_to_file_map(
        tree, parts, st, dest, is_dir_dest=is_dir_dest, file_map=file_map,
        uid=uid, gid=gid, mode_override=mode_override,
        auto_extract=auto_extract, src_rel=rel,
        ignore_patterns=engine.ignore_patterns, spool=spool,
    )


def _copy_from_rootfs(from_rootfs, src, dest, is_dir_dest,
                      file_map, uid, gid, mode_override):
    """COPY --from a stage or image rootfs, confined to that rootfs.

    The source tree here is image content outright, so the walk matters
    twice over: `/escape/file` with `escape -> /some/host/path` shipped
    in the image used to read the host's file and pack it into the
    layer, without the Dockerfile or the build context saying anything
    unusual. Clamped, the link means inside the image the way it does
    to the guest.
    """
    tree = _SourceTree(from_rootfs)
    raw = _spec_parts(src)
    if raw is None:
        raise BuildError(
            f"COPY --from source '{src}' escapes the source rootfs."
        )
    parts = tree.resolve(raw)
    st = tree.lstat(parts) if parts is not None else None
    if st is None:
        raise BuildError(
            f"COPY --from source '{src}' not found in stage."
        )
    _add_to_file_map(
        tree, parts, st, dest, is_dir_dest=is_dir_dest, file_map=file_map,
        uid=uid, gid=gid, mode_override=mode_override,
        auto_extract=False, src_rel=_rel_name(parts),
        ignore_patterns=(),
    )


def _copy_url(url, dest, file_map, uid, gid, mode_override, spool):
    """ADD URL: download the file to dest.

    Streamed onto disk rather than read whole: how much a URL answers with
    is the remote's choice, and the response used to be held in memory
    until the whole instruction was packed.
    """
    if dest.endswith("/"):
        name = os.path.basename(
            urllib.parse.urlparse(url).path
        ) or "index"
        arcname = (dest.lstrip("/") + name)
    else:
        arcname = dest.lstrip("/")
    opener = urllib.request.build_opener(AuthStrippingRedirectHandler)
    try:
        with opener.open(url) as resp:
            path = _spool_stream(resp, spool)
    except (urllib.error.URLError, OSError) as exc:
        raise BuildError(f"ADD {url}: {exc}") from exc
    _spool_entry(
        file_map, arcname, path,
        mode_override if mode_override is not None else 0o644,
        uid, gid, int(time.time()),
    )


def _add_to_file_map(tree, parts, st, dest, is_dir_dest, file_map,
                     uid, gid, mode_override, auto_extract, src_rel,
                     ignore_patterns, spool=None):
    """Record the source *parts* names in *tree*, by what the lstat says.

    A symlink is copied as a symlink (never read through), a directory
    is walked, and a regular file is recorded — or, for ADD, unpacked
    when it turns out to be an archive. Devices, FIFOs and sockets are
    skipped, as they are everywhere else in the program.
    """
    if stat.S_ISLNK(st.st_mode):
        _add_symlink(tree, parts, st, dest, is_dir_dest, file_map, uid, gid)
        return
    if stat.S_ISDIR(st.st_mode):
        _add_directory_tree(
            tree, parts, dest, file_map, uid, gid, mode_override, src_rel,
            ignore_patterns,
        )
        return
    if stat.S_ISREG(st.st_mode):
        if auto_extract and _extract_archive(
                tree, parts, dest, file_map, uid, gid, spool):
            return
        _add_regular(tree, parts, st, dest, is_dir_dest, file_map,
                     uid, gid, mode_override)


def _add_regular(tree, parts, st, dest, is_dir_dest, file_map,
                 uid, gid, mode_override):
    arcname = _dest_arcname(parts[-1], dest, is_dir_dest)
    mode = stat.S_IMODE(st.st_mode)
    if mode_override is not None:
        mode = mode_override
    _file_entry(file_map, arcname, tree.root, parts,
                mode, uid, gid, int(st.st_mtime), st.st_size)


def _add_symlink(tree, parts, st, dest, is_dir_dest, file_map, uid, gid):
    arcname = _dest_arcname(parts[-1], dest, is_dir_dest)
    try:
        fd = tree.opendir(parts[:-1])
    except OSError:
        return
    try:
        target = os.readlink(parts[-1], dir_fd=fd)
    except OSError:
        return
    finally:
        os.close(fd)
    file_map[arcname] = {
        "kind": "symlink", "target": target,
        "mode": 0o777, "uid": uid, "gid": gid,
        "mtime": int(st.st_mtime),
    }


def _add_directory_tree(tree, parts, dest, file_map,
                        uid, gid, mode_override, src_rel,
                        ignore_patterns):
    """Record everything under the directory *parts* names.

    When the source is a directory its entries themselves go into dest,
    so the destination is treated as a directory.

    The walk carries directory descriptors on an explicit stack, one
    level opened O_NOFOLLOW off the one above: a symlink is recorded as
    a symlink and never descended (what os.walk(followlinks=False)
    gave), and how deep the tree goes is the context's business rather
    than the interpreter's — of the descriptor table's, too, which is why
    the levels holding one at a time are bounded (dirfd.Levels): a walk
    that runs out partway down records nothing below that point, and what
    it records is what the instruction copies and what the layer holds.
    """
    if not dest.endswith("/"):
        dest = dest + "/"
    try:
        top_fd = tree.opendir(parts)
    except OSError:
        return
    prefix = list(parts)
    # Frame layout: [fd, None, pending names, rel components, owned].
    stack = [[top_fd, None, None, (), True]]
    levels = dirfd.Levels(stack)
    try:
        while stack:
            frame = stack[-1]
            fd, _, pending, rel_parts, owned = frame
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
            try:
                st = dirfd.lstat_at(fd, name)
            except OSError:
                continue
            child = rel_parts + (name,)
            combined = (
                src_rel + "/" + "/".join(child)
                if src_rel and src_rel != os.curdir else "/".join(child)
            )
            if is_ignored(combined, ignore_patterns):
                continue
            arc = _make_subpath(dest, "/".join(rel_parts), name).lstrip("/")
            mode = st.st_mode
            if stat.S_ISLNK(mode):
                try:
                    target = os.readlink(name, dir_fd=fd)
                except OSError:
                    continue
                file_map[arc] = {
                    "kind": "symlink", "target": target,
                    "mode": 0o777, "uid": uid, "gid": gid,
                    "mtime": int(st.st_mtime),
                }
            elif stat.S_ISDIR(mode):
                file_map[arc] = {
                    "kind": "dir",
                    "mode": (mode_override if mode_override is not None
                             else stat.S_IMODE(mode)),
                    "uid": uid, "gid": gid, "mtime": 0,
                }
                try:
                    sub = dirfd.opendir_at(fd, name)
                except OSError:
                    continue
                levels.push([sub, None, None, child, True])
            elif stat.S_ISREG(mode):
                _file_entry(
                    file_map, arc, tree.root, prefix + list(child),
                    (mode_override if mode_override is not None
                     else stat.S_IMODE(mode)),
                    uid, gid, int(st.st_mtime), st.st_size,
                )
            # Other types intentionally skipped (devices, FIFOs, sockets).
    except BaseException:
        dirfd.close_frames(stack)
        raise


def _make_subpath(dest, rel, name):
    parts = [dest.rstrip("/")]
    if rel and rel != ".":
        parts.append(rel)
    if name:
        parts.append(name)
    return "/".join(p.strip("/") for p in parts if p is not None)


def _dest_arcname(src_full, dest, is_dir_dest):
    if is_dir_dest or dest.endswith("/"):
        base = os.path.basename(src_full.rstrip("/"))
        return (dest.rstrip("/") + "/" + base).lstrip("/")
    return dest.lstrip("/")


def _extract_archive(tree, parts, dest, file_map, uid, gid, spool):
    """ADD auto-extract: unpack *parts* into dest when it is a tar.

    True when it was one and the members were recorded. Sniffed and read
    through a single descriptor on the file, so the archive that gets
    unpacked is the inode the walk found and not whatever the name leads
    to by the time tarfile opens it.
    """
    try:
        fd, _st = tree.open_file(parts)
    except OSError:
        return False
    try:
        with open(fd, "rb", closefd=False) as fh:
            if not is_tar_header(fh.read(TAR_HEADER_BYTES)):
                return False
            fh.seek(0)
            _extract_tar_into_dest(fh, dest, file_map, uid, gid, spool)
            return True
    finally:
        os.close(fd)


def _extract_tar_into_dest(fobj, dest, file_map, uid, gid, spool):
    """ADD auto-extract: stream the tar in *fobj* into dest as a tree.

    Every regular member is spooled to its own file. Reading them into the
    file_map instead meant the archive's entire uncompressed content sat in
    memory at once -- and the archive is whatever the Dockerfile pointed
    ADD at, which for a URL source is not even local.
    """
    if not dest.endswith("/"):
        dest = dest + "/"
    with tarfile.open(fileobj=fobj, mode="r|*") as tf:
        for m in tf:
            if m.isblk() or m.ischr() or m.isfifo():
                continue
            # Strip a literal leading './' prefix (not lstrip("./") — that
            # would eat any combination of dots and slashes and silently
            # neutralise './../foo' style traversal entries).
            rel = m.name
            while rel.startswith("./"):
                rel = rel[2:]
            rel = rel.lstrip("/")
            if any(p in ("..", ".", "") for p in rel.split("/")):
                continue
            arc = (dest + rel).lstrip("/")
            if m.isdir():
                file_map[arc] = {
                    "kind": "dir",
                    "mode": stat.S_IMODE(m.mode) or 0o755,
                    "uid": uid, "gid": gid, "mtime": int(m.mtime),
                }
            elif m.issym():
                file_map[arc] = {
                    "kind": "symlink", "target": m.linkname,
                    "mode": 0o777, "uid": uid, "gid": gid,
                    "mtime": int(m.mtime),
                }
            elif m.isreg():
                fobj_m = tf.extractfile(m)
                if fobj_m is None:
                    continue
                path = _spool_stream(fobj_m, spool)
                _spool_entry(
                    file_map, arc, path,
                    stat.S_IMODE(m.mode) or 0o644, uid, gid, int(m.mtime),
                )


def _materialise_files(rootfs_dir, file_map):
    """Apply file_map entries to rootfs_dir on disk.

    Sorting the arcnames guarantees every parent is materialised before
    its children, so a symlink entry lands before anything written
    "through" it. The destination's parent is then resolved with
    safe_resolve_parts, which follows existing symlink components but
    clamps each hop inside rootfs_dir — otherwise an ADD'd tar (or a
    stage) could ship `evil -> /` followed by `evil/passwd` and the write
    would escape onto the host.

    The resolve says where the entry belongs; it does not make writing
    there safe on its own, because it decides that by name and everything
    afterwards used the answer by name too. os.makedirs(), os.remove(),
    shutil.copyfile() and os.chmod() all resolve the path again, so a
    component re-pointed in between — by a background process an earlier
    RUN left running, which off Termux nothing kills — sent the whole
    instruction wherever the new link led. The parent is therefore
    re-walked off a descriptor (dirfd.opendir_under, O_NOFOLLOW per
    level, creating what is missing) and the entry itself is written as
    (dir_fd, name).

    The final component is deliberately not resolved, so we replace the
    entry itself and never a same-named symlink's target — which means
    every kind has to drop a link standing there first, the directory
    branch included.

    The reading half is the same bargain: a file entry's bytes come out
    of a descriptor MapSources walks down from the tree the source was
    found in, never out of a path composed from it.
    """
    with MapSources() as sources:
        for arcname in sorted(file_map.keys()):
            entry = file_map[arcname]
            # Same rule the layer is packed by, so the tree and the tar
            # agree on what the instruction produced (see
            # layer_diff.layer_path_parts).
            parts = layer_path_parts(arcname)
            if parts is None:
                continue
            resolved = safe_resolve_parts(rootfs_dir, parts[:-1])
            if resolved is None:
                continue

            dir_fd = dirfd.opendir_under(rootfs_dir, resolved, create=True)
            if dir_fd is None:
                raise BuildError(
                    f"Failed to write '{arcname}' into rootfs: "
                    f"'{'/'.join(resolved)}' is not a directory inside it"
                )
            try:
                _materialise_entry(dir_fd, parts[-1], entry, sources)
            except OSError as exc:
                raise BuildError(
                    f"Failed to write '{arcname}' into rootfs: {exc}"
                ) from exc
            finally:
                os.close(dir_fd)


def _drop_entry_at(dir_fd, name):
    """Remove whatever holds *name*, ignoring failure — as os.remove did."""
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


def _materialise_entry(dir_fd, name, entry, sources):
    """Write one file_map entry into the directory dir_fd refers to."""
    kind = entry["kind"]
    if kind == "dir":
        # A symlink already standing at this name would send both the
        # mkdir and the chmod to whatever it points at. The parent is
        # resolved with clamping but the final component is deliberately
        # left alone, so `etc -> /home/user` in the image plus an ADD'd
        # tar carrying an `etc/` member had that host directory chmod'ed
        # to the member's mode -- and the tree then disagreed with the
        # layer, which records a plain directory there. Overlay semantics
        # replace a symlink with a real directory; the tar extractor
        # already drops it the same way (see helpers/tar_extract).
        try:
            st = dirfd.lstat_at(dir_fd, name)
        except OSError:
            st = None
        if st is not None and stat.S_ISLNK(st.st_mode):
            _drop_entry_at(dir_fd, name)
        try:
            os.mkdir(name, 0o777, dir_fd=dir_fd)
        except FileExistsError:
            pass
        # chmod_at opens O_PATH|O_NOFOLLOW and sets the mode on the
        # descriptor: fchmodat(2) has no AT_SYMLINK_NOFOLLOW, so naming
        # the entry would hand the mode to a link planted since the mkdir.
        dirfd.chmod_at(dir_fd, name, entry.get("mode", 0o755), only_dir=True)
    elif kind == "symlink":
        # symlink(2) has no O_TRUNC; whatever is there has to go first.
        _drop_entry_at(dir_fd, name)
        os.symlink(entry["target"], name, dir_fd=dir_fd)
    elif kind == "file":
        src_fd, _src_st = sources.open(entry)
        try:
            # open_new_at is O_EXCL and drops a leftover rather than
            # adopting it, so the bytes always land in a new inode inside
            # this directory -- never through a hardlink to somewhere
            # else, which is the one thing O_NOFOLLOW cannot refuse.
            fd, _st = dirfd.open_new_at(dir_fd, name, entry.get("mode", 0o644))
            try:
                with open(src_fd, "rb", closefd=False) as src, \
                        os.fdopen(fd, "wb", closefd=False) as dst:
                    shutil.copyfileobj(src, dst, _SPOOL_CHUNK)
                # Explicitly, because the mode open_new_at created the
                # file with went through the umask.
                try:
                    os.fchmod(fd, entry.get("mode", 0o644))
                except OSError:
                    pass
            finally:
                os.close(fd)
        finally:
            os.close(src_fd)
