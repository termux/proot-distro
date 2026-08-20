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
    is_tar_archive, looks_like_url,
)
from proot_distro.helpers.build_engine.users import resolve_chown
from proot_distro.helpers.docker import (
    AuthStrippingRedirectHandler, layer_cache_path, pull_image,
)
from proot_distro.helpers.layer_diff import (
    layer_path_parts, write_files_layer,
)
from proot_distro.helpers.tar_extract import safe_resolve_parts
from proot_distro import dirfd


# Chunk size for spooling, the same one tar_extract streams with.
_SPOOL_CHUNK = 1 << 17


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
    file_map[arcname] = {
        "kind": "file", "src": path,
        "mode": mode, "uid": uid, "gid": gid, "mtime": mtime,
    }


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
    final_path = layer_cache_path(digest)
    os.makedirs(os.path.dirname(final_path), exist_ok=True)
    os.replace(tmp_layer_path, final_path)
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
        pull_image(image_ref, rootfs, engine.target_arch_pd)
    except RuntimeError as exc:
        raise BuildError(f"COPY --from={image_ref}: {exc}") from exc
    return rootfs


def _copy_from_context(engine, src, dest, is_dir_dest, file_map,
                       uid, gid, mode_override, auto_extract, spool=None):
    # Per Docker semantics, a leading '/' on a COPY/ADD source is
    # equivalent to no leading slash: both forms resolve relative
    # to the build context root.
    src_rel_raw = src.lstrip("/")

    full = os.path.normpath(os.path.join(engine.build_dir, src_rel_raw))
    if (full != engine.build_dir
            and not full.startswith(engine.build_dir + os.sep)):
        raise BuildError(
            f"COPY source '{src}' escapes the build context."
        )
    if not os.path.exists(full):
        matches = sorted(simple_glob(engine.build_dir, src_rel_raw))
        matches = [m for m in matches if not is_ignored(m, engine.ignore_patterns)]
        if not matches:
            raise BuildError(
                f"COPY/ADD source '{src}' not found in build context."
            )
        for m in matches:
            full_m = os.path.join(engine.build_dir, m)
            _add_to_file_map(
                full_m, dest, is_dir_dest=True, file_map=file_map,
                uid=uid, gid=gid, mode_override=mode_override,
                auto_extract=auto_extract, src_rel=m,
                ignore_patterns=engine.ignore_patterns, spool=spool,
            )
        return
    rel = os.path.relpath(full, engine.build_dir)
    if is_ignored(rel, engine.ignore_patterns):
        return
    _add_to_file_map(
        full, dest, is_dir_dest=is_dir_dest, file_map=file_map,
        uid=uid, gid=gid, mode_override=mode_override,
        auto_extract=auto_extract, src_rel=rel,
        ignore_patterns=engine.ignore_patterns, spool=spool,
    )


def _copy_from_rootfs(from_rootfs, src, dest, is_dir_dest,
                      file_map, uid, gid, mode_override):
    abs_rootfs = os.path.abspath(from_rootfs)
    full = os.path.normpath(os.path.join(abs_rootfs, src.lstrip("/")))
    if full != abs_rootfs and not full.startswith(abs_rootfs + os.sep):
        raise BuildError(
            f"COPY --from source '{src}' escapes the source rootfs."
        )
    if not os.path.lexists(full):
        raise BuildError(
            f"COPY --from source '{src}' not found in stage."
        )
    _add_to_file_map(
        full, dest, is_dir_dest=is_dir_dest, file_map=file_map,
        uid=uid, gid=gid, mode_override=mode_override,
        auto_extract=False, src_rel=src,
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


def _add_to_file_map(src_full, dest, is_dir_dest, file_map,
                     uid, gid, mode_override, auto_extract, src_rel,
                     ignore_patterns, spool=None):
    if os.path.islink(src_full):
        _add_symlink(src_full, dest, is_dir_dest, file_map, uid, gid)
        return
    if os.path.isdir(src_full):
        _add_directory_tree(
            src_full, dest, file_map, uid, gid, mode_override, src_rel,
            ignore_patterns,
        )
        return
    if os.path.isfile(src_full):
        # Auto-extract tar archives for ADD.
        if auto_extract and is_tar_archive(src_full):
            _extract_tar_into_dest(src_full, dest, file_map, uid, gid, spool)
            return
        _add_regular(src_full, dest, is_dir_dest, file_map,
                     uid, gid, mode_override, src_rel)
        return


def _add_regular(src_full, dest, is_dir_dest, file_map,
                 uid, gid, mode_override, src_rel):
    arcname = _dest_arcname(src_full, dest, is_dir_dest)
    try:
        mode = stat.S_IMODE(os.lstat(src_full).st_mode)
    except OSError:
        mode = 0o644
    if mode_override is not None:
        mode = mode_override
    file_map[arcname] = {
        "kind": "file", "src": src_full,
        "mode": mode, "uid": uid, "gid": gid,
        "mtime": int(os.lstat(src_full).st_mtime),
    }


def _add_symlink(src_full, dest, is_dir_dest, file_map, uid, gid):
    arcname = _dest_arcname(src_full, dest, is_dir_dest)
    try:
        target = os.readlink(src_full)
    except OSError:
        return
    file_map[arcname] = {
        "kind": "symlink", "target": target,
        "mode": 0o777, "uid": uid, "gid": gid,
        "mtime": int(os.lstat(src_full).st_mtime),
    }


def _add_directory_tree(src_full, dest, file_map,
                        uid, gid, mode_override, src_rel,
                        ignore_patterns):
    # When source is a directory, the entries themselves go into
    # dest. The destination is treated as a directory.
    if not dest.endswith("/"):
        dest = dest + "/"
    for dirpath, dirnames, filenames in os.walk(src_full, followlinks=False):
        rel = os.path.relpath(dirpath, src_full)
        for d in list(dirnames):
            full = os.path.join(dirpath, d)
            if os.path.islink(full):
                arc = _make_subpath(dest, rel, d).lstrip("/")
                try:
                    tgt = os.readlink(full)
                except OSError:
                    continue
                file_map[arc] = {
                    "kind": "symlink", "target": tgt,
                    "mode": 0o777, "uid": uid, "gid": gid,
                    "mtime": 0,
                }
                dirnames.remove(d)
        # Add the directory itself (except the root).
        if rel != ".":
            arc = _make_subpath(dest, rel, "").rstrip("/").lstrip("/")
            if arc:
                try:
                    mode = stat.S_IMODE(os.lstat(dirpath).st_mode)
                except OSError:
                    mode = 0o755
                file_map[arc] = {
                    "kind": "dir",
                    "mode": mode_override if mode_override is not None else mode,
                    "uid": uid, "gid": gid, "mtime": 0,
                }
        for f in filenames:
            full = os.path.join(dirpath, f)
            src_relpath = os.path.relpath(full, src_full)
            combined_rel = (
                (src_rel + "/" + src_relpath)
                if src_rel and src_rel != "." else src_relpath
            )
            if is_ignored(combined_rel, ignore_patterns):
                continue
            arc = _make_subpath(dest, rel, f).lstrip("/")
            if os.path.islink(full):
                try:
                    tgt = os.readlink(full)
                except OSError:
                    continue
                file_map[arc] = {
                    "kind": "symlink", "target": tgt,
                    "mode": 0o777, "uid": uid, "gid": gid,
                    "mtime": int(os.lstat(full).st_mtime),
                }
            else:
                try:
                    mode = stat.S_IMODE(os.lstat(full).st_mode)
                except OSError:
                    mode = 0o644
                if mode_override is not None:
                    mode = mode_override
                file_map[arc] = {
                    "kind": "file", "src": full,
                    "mode": mode, "uid": uid, "gid": gid,
                    "mtime": int(os.lstat(full).st_mtime),
                }


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


def _extract_tar_into_dest(src_full, dest, file_map, uid, gid, spool):
    """ADD auto-extract: stream the tar into dest as a tree.

    Every regular member is spooled to its own file. Reading them into the
    file_map instead meant the archive's entire uncompressed content sat in
    memory at once -- and the archive is whatever the Dockerfile pointed
    ADD at, which for a URL source is not even local.
    """
    if not dest.endswith("/"):
        dest = dest + "/"
    with tarfile.open(src_full, "r|*") as tf:
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
                fobj = tf.extractfile(m)
                if fobj is None:
                    continue
                path = _spool_stream(fobj, spool)
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
    """
    for arcname in sorted(file_map.keys()):
        entry = file_map[arcname]
        # Same rule the layer is packed by, so the tree and the tar agree
        # on what the instruction produced (see layer_diff.layer_path_parts).
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
            _materialise_entry(dir_fd, parts[-1], entry)
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


def _materialise_entry(dir_fd, name, entry):
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
        # open_new_at is O_EXCL and drops a leftover rather than adopting
        # it, so the bytes always land in a new inode inside this
        # directory -- never through a hardlink to somewhere else, which
        # is the one thing O_NOFOLLOW cannot refuse.
        fd, _st = dirfd.open_new_at(dir_fd, name, entry.get("mode", 0o644))
        try:
            with open(entry["src"], "rb") as src, \
                    os.fdopen(fd, "wb", closefd=False) as dst:
                shutil.copyfileobj(src, dst, _SPOOL_CHUNK)
            # Explicitly, because the mode open_new_at created the file
            # with went through the umask.
            try:
                os.fchmod(fd, entry.get("mode", 0o644))
            except OSError:
                pass
        finally:
            os.close(fd)
