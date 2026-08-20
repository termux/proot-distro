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
# Architecture: Two deletion targets behind one command.
#
#   default   — an entire container directory (rootfs + manifest),
#               removed recursively with permissions fixed on the fly so
#               subtrees that were chmod-000'd inside the container can
#               still be cleared.
#   --image   — a cached OCI image: its manifest-cache entry plus every
#               layer blob no remaining cached image still references.
#               Installed containers are untouched — their rootfs is an
#               independent copy — but `reset` will need the network
#               again, which the summary points out.
#
# An image identifier is a reference ('ubuntu:24.04', tag defaulting to
# ':latest'), an image ID prefix as shown by `list --image`, or a cache
# key. A reference matches every architecture variant cached under it
# unless --architecture narrows the selection.

import json
import os
import stat
import sys
from contextlib import ExitStack

from proot_distro import dirfd
from proot_distro.arch import normalize_arch
from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.message import C, msg, log_info, log_error, crit_error
from proot_distro.locking import BuildLock, ContainerLock
from proot_distro.names import require_valid_name
from proot_distro.paths import container_dir, container_manifest, container_rootfs
from proot_distro.progress import fmt_size
from proot_distro.helpers.docker import (
    ARCH_TO_DOCKER,
    canonical_ref,
    image_cache_entry,
    iter_cached_images,
    layer_cache_path,
    parse_image_ref,
    with_explicit_tag,
)

# Minimum length of an image-ID prefix accepted as an identifier. Short
# enough to stay convenient, long enough that a typo is unlikely to name
# an unrelated image by accident.
_MIN_ID_PREFIX = 4


def _unlink_entry(dir_fd, name, path, st, on_remove) -> bool:
    """Remove the non-directory *name* under dir_fd. True when it is gone."""
    if not stat.S_ISLNK(st.st_mode):
        needed = stat.S_IRUSR | stat.S_IWUSR
        mode = stat.S_IMODE(st.st_mode)
        if mode | needed != mode:
            dirfd.chmod_at(dir_fd, name, mode | needed)
    try:
        os.unlink(name, dir_fd=dir_fd)
    except FileNotFoundError:
        return True
    except OSError:
        return False
    if on_remove:
        on_remove(path)
    return True


def _open_for_removal(dir_fd, name, st):
    """Open the directory *name* under dir_fd so its contents can go.

    A subtree the container chmod'ed 000 is relaxed first, through a
    descriptor rather than through its name (dirfd.chmod_at). Returns the
    fd, or None when the directory still cannot be listed.
    """
    needed = stat.S_IRWXU
    mode = stat.S_IMODE(st.st_mode)
    if mode | needed != mode:
        dirfd.chmod_at(dir_fd, name, mode | needed, only_dir=True)
    try:
        return dirfd.opendir_at(dir_fd, name)
    except OSError:
        return None


def _remove_path(path: str, on_remove=None) -> bool:
    """Remove path recursively, fixing permissions on the fly.

    Returns True on full success. Any failure returns False and the partial
    state is left on disk. The optional on_remove callback is called with the
    path of each successfully removed entry.

    The descent carries its open directories on an explicit stack rather
    than recursing: how deep a rootfs goes is the container's choice, and a
    tree a little past the interpreter's limit — which a guest can build in
    a second — ended `remove` and `reset` in a RecursionError traceback.
    That is not an OSError, so nothing here caught it.

    Entries are named as (directory fd, name) throughout, which also keeps
    the chmod that relaxes a sealed subtree off a path: os.chmod() follows
    a symlink, and the mode change would land on whatever it pointed at.
    """
    path = os.path.abspath(path)
    name = os.path.basename(path)
    if not name:
        return False                    # "/" — not something to remove
    try:
        parent_fd = dirfd.opendir(os.path.dirname(path))
    except FileNotFoundError:
        return True
    except OSError:
        return False
    try:
        return _remove_at(parent_fd, name, path, on_remove)
    finally:
        os.close(parent_fd)


def _remove_at(parent_fd, name, path, on_remove) -> bool:
    """Remove *name* under parent_fd, descending into it if it is a directory."""
    try:
        st = dirfd.lstat_at(parent_fd, name)
    except FileNotFoundError:
        return True
    except OSError:
        return False

    if not stat.S_ISDIR(st.st_mode):
        return _unlink_entry(parent_fd, name, path, st, on_remove)

    fd = _open_for_removal(parent_fd, name, st)
    if fd is None:
        return False

    ok = True
    # Frame layout: [fd, None, parent fd, own name, own path, pending,
    # emptied, owned] — the two descriptor slots first and `owned` last, so
    # close_frames() unwinds an interrupted walk. `emptied` stays True only
    # while every entry below this level has gone; a directory that still
    # holds something is not rmdir'ed, exactly as before.
    stack = [[fd, None, parent_fd, name, path, None, True, True]]
    try:
        while stack:
            frame = stack[-1]
            fd, _, pfd, entry, epath, pending, _, _ = frame
            if pending is None:
                try:
                    pending = dirfd.listdir_at(fd)
                except OSError:
                    pending = []
                    frame[6] = False
                pending.reverse()
                frame[5] = pending
            if not pending:
                stack.pop()
                os.close(fd)
                if frame[6]:
                    try:
                        os.rmdir(entry, dir_fd=pfd)
                        if on_remove:
                            on_remove(epath)
                    except OSError:
                        frame[6] = False
                if not frame[6]:
                    ok = False
                    if stack:
                        stack[-1][6] = False
                continue

            child = pending.pop()
            child_path = os.path.join(epath, child)
            try:
                child_st = dirfd.lstat_at(fd, child)
            except FileNotFoundError:
                continue                # went away on its own
            except OSError:
                frame[6] = False
                continue
            if not stat.S_ISDIR(child_st.st_mode):
                if not _unlink_entry(fd, child, child_path, child_st,
                                     on_remove):
                    frame[6] = False
                continue
            sub = _open_for_removal(fd, child, child_st)
            if sub is None:
                frame[6] = False
                continue
            stack.append([sub, None, fd, child, child_path, None, True, True])
    except BaseException:
        dirfd.close_frames(stack)
        raise

    return ok


def command_remove(args) -> None:
    """Delete an installed container, or a cached image with --image."""
    if getattr(args, "image", False):
        _remove_image(args)
        return
    if getattr(args, "override_arch", None):
        # Containers have exactly one architecture, so silently ignoring
        # this would hide a forgotten --image.
        crit_error("'--architecture' selects between cached image "
                   "variants; it requires '--image'.")
        sys.exit(1)
    _remove_container(args)


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------

def _remove_container(args) -> None:
    """Delete an installed container's directory tree."""
    container_name = args.target
    verbose = getattr(args, "verbose", False)

    require_valid_name(container_name)

    rootfs_dir = container_rootfs(container_name)

    if not os.path.isdir(rootfs_dir):
        crit_error(f"container '{container_name}' is not installed.")
        sys.exit(1)

    with ContainerLock(container_name, exclusive=True, command="remove"):
        log_info(f"Removing container '{container_name}'...")

        on_remove = None
        if verbose:
            def on_remove(path):
                log_info(f"Removed: '{path}'")

        if not _remove_path(container_dir(container_name), on_remove):
            log_error("Finished with errors. Some files probably were not "
                      "deleted.")
            sys.exit(1)

    log_info("Finished removing the container.")


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def _remove_image(args) -> None:
    """Delete a cached image and the layer blobs it alone held."""
    identifier = args.target
    verbose = getattr(args, "verbose", False)

    raw_arch = getattr(args, "override_arch", None)
    arch = None
    if raw_arch:
        arch = normalize_arch(raw_arch)
        if arch is None:
            crit_error(f"unknown architecture '{raw_arch}'. "
                       f"Valid values: aarch64, arm, i686, riscv64, x86_64 "
                       f"(or Docker format: linux/arm64, linux/amd64, "
                       f"linux/arm/v7, linux/386, linux/riscv64).")
            sys.exit(1)

    targets = _resolve_images(identifier, arch)
    if not targets:
        _report_no_match(identifier, arch)
        sys.exit(1)

    # Hold the same lock `build` and `push` take for these images, so an
    # image cannot be deleted while it is being produced or uploaded.
    with ExitStack() as locks:
        for lock in _image_locks(identifier, targets):
            locks.enter_context(lock)
        _delete_images(targets, verbose)


def _resolve_images(identifier: str, arch) -> list:
    """Return the cached-image records addressed by *identifier*.

    A reference is resolved through the cache key first — that works for
    entries which do not record a reference of their own — and matches
    every architecture variant unless *arch* narrows it. Only when a
    reference resolves to nothing is the identifier tried as an image ID
    prefix or a cache key.
    """
    archs = [arch] if arch else list(ARCH_TO_DOCKER)

    ref = with_explicit_tag(identifier)
    matches, seen = [], set()
    for candidate in archs:
        record = image_cache_entry(ref, candidate)
        if record is not None and record["path"] not in seen:
            seen.add(record["path"])
            matches.append(record)
    if matches:
        return matches

    wanted = identifier.lower()
    for record in iter_cached_images():
        if arch and record["arch"] and record["arch"] != arch:
            continue
        if record["key"] == wanted:
            matches.append(record)
        elif (len(wanted) >= _MIN_ID_PREFIX
                and record["image_id"].lower().startswith(wanted)):
            matches.append(record)

    ids = {record["image_id"] for record in matches}
    if len(ids) > 1:
        crit_error(f"image ID '{identifier}' is ambiguous - it matches "
                   f"{len(ids)} images:")
        _print_image_list(matches)
        msg()
        sys.exit(1)
    return matches


def _image_locks(identifier: str, targets: list) -> list:
    """Return the BuildLocks to hold while *targets* are deleted.

    `build` and `push` key their lock on the reference as the user
    spelled it, so both the recorded reference and the one just typed
    are locked; duplicates collapse. Locks are entered in sorted-path
    order, consistent with every other multi-lock call site.
    """
    locks = {}
    for record in targets:
        if not record["arch"]:
            continue
        for ref in (record["image_ref"], with_explicit_tag(identifier)):
            if not ref:
                continue
            lock = BuildLock(ref, record["arch"], command="remove")
            locks.setdefault(lock.lock_path, lock)
    return [locks[path] for path in sorted(locks)]


def _delete_images(targets: list, verbose: bool) -> None:
    """Delete the manifest entries in *targets* plus their orphan layers."""
    for record in targets:
        log_info(f"Removing image '{_display(record)}' "
                 f"({record['arch'] or 'unknown arch'})...")

    failed = False
    reclaimed = 0
    for record in targets:
        size = _file_size(record["path"])
        try:
            os.remove(record["path"])
        except OSError as exc:
            log_error(f"Cannot remove '{record['path']}': {exc}")
            failed = True
            continue
        reclaimed += size
        if verbose:
            log_info(f"Removed: '{record['path']}'")

    # Layers are shared between images, so a blob may only go once no
    # surviving manifest lists it. The inventory is re-read here, after
    # the deletions above, so it reflects exactly what is left.
    keep = set()
    for record in iter_cached_images():
        keep.update(layer["digest"] for layer in record["layers"])

    dropped = set()
    for record in targets:
        for layer in record["layers"]:
            digest = layer["digest"]
            if digest in keep or digest in dropped:
                continue
            dropped.add(digest)
            try:
                path = layer_cache_path(digest)
            except RuntimeError:
                continue
            size = _file_size(path)
            try:
                os.remove(path)
            except FileNotFoundError:
                continue
            except OSError as exc:
                log_error(f"Cannot remove '{path}': {exc}")
                failed = True
                continue
            reclaimed += size
            if verbose:
                log_info(f"Removed: '{path}'")

    dependents = _containers_from_images(targets)
    if dependents:
        names = ", ".join(f"'{name}'" for name in dependents)
        log_info(f"Container(s) {names} were installed from this image and "
                 f"keep working.")

    if failed:
        log_error("Finished with errors. Some files probably were not "
                  "deleted.")
        sys.exit(1)

    log_info(f"Reclaimed {fmt_size(reclaimed)} of disk space.")


def _containers_from_images(targets: list) -> list:
    """Return the names of installed containers created from *targets*."""
    wanted = {
        (canonical_ref(record["image_ref"]), record["arch"])
        for record in targets if record["image_ref"]
    }
    if not wanted:
        return []

    found = []
    try:
        names = sorted(os.listdir(CONTAINERS_DIR))
    except OSError:
        return found
    for name in names:
        try:
            with open(container_manifest(name)) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        ref, arch = data.get("image_ref"), data.get("arch")
        if ref and (canonical_ref(ref), arch) in wanted:
            found.append(name)
    return found


def _report_no_match(identifier: str, arch) -> None:
    """Explain an identifier that addressed nothing, with the near misses."""
    scope = f" for architecture '{arch}'" if arch else ""
    crit_error(f"no cached image matches '{identifier}'{scope}.")

    # Deliberately unfiltered by architecture: when the only copy is
    # cached for another one, seeing it listed with its arch is exactly
    # the answer the user needs.
    similar = [
        record for record in iter_cached_images()
        if _same_name(record, identifier)
    ]
    msg()
    if similar:
        msg(f"{C['CYAN']}Cached images with that name:{C['RST']}")
        _print_image_list(similar)
    else:
        msg(f"{C['CYAN']}List cached images: "
            f"{C['GREEN']}{PROGRAM_NAME} list --image{C['RST']}")
    msg()


def _same_name(record: dict, identifier: str) -> bool:
    """Return True when *record* names the same repository as *identifier*.

    Used only to suggest alternatives, so the comparison is deliberately
    loose: the tag is ignored, and a bare name matches any registry.
    """
    ref = record["image_ref"] or record["repo"]
    if not ref:
        return False
    repo = parse_image_ref(ref)[1]
    wanted = parse_image_ref(identifier)[1]
    return repo == wanted or repo.split("/")[-1] == wanted.split("/")[-1]


def _print_image_list(records: list) -> None:
    """Print records as a bullet list of 'ref (arch)' lines."""
    msg()
    for record in records:
        msg(f"  {C['CYAN']}* {C['GREEN']}{_display(record)}"
            f"{C['CYAN']} ({record['arch'] or 'unknown arch'}, "
            f"ID {record['image_id'][:12] or record['key']}){C['RST']}")


def _display(record: dict) -> str:
    """Return the reference to name *record* by in messages."""
    if record["image_ref"]:
        return record["image_ref"]
    return f"{record['repo']}:<none>" if record["repo"] else record["key"]


def _file_size(path: str) -> int:
    """Return the size of *path*, or 0 when it cannot be determined."""
    try:
        return os.path.getsize(path)
    except OSError:
        return 0
