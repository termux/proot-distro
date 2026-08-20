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

# Architecture: Three deletion targets behind one command.
#
#   default        — the entire download cache directory contents.
#                    Failures on individual entries are reported but do
#                    not abort the operation, so partial caches are
#                    still cleaned as much as possible.
#   --orphan       — only the layer blobs nothing references any more.
#                    Every other file, including the manifest cache and
#                    the build index, is left exactly as it was.
#   --build-cache  — the build index, followed by that same sweep with
#                    the index no longer counted as a reference.
#
# A blob is referenced when a cached image names it (manifest cache) or
# when a build-cache entry pins it (build index). The second half matters:
# a multi-stage intermediate, or a step whose image has since been
# rebuilt under a different tag, appears in no manifest at all, and
# collecting it would silently empty the build cache — which is what
# --build-cache is for, and what a plain `clear-cache` does on its way
# past. Installed containers are deliberately not roots: their rootfs is
# an independent copy, the same reasoning `remove --image` already
# applies.
#
# --build-cache is that one sweep with a root removed rather than a
# second algorithm, and it never reads the index: it unlinks it and
# computes the keep set from the manifest cache alone. An index too
# corrupt to parse is among the reasons to reach for the flag, and
# deriving a delete set from it would fail exactly there. Order matters
# in one direction only — the index goes first, so a failure to remove
# it stops the command before a blob it still pins is deleted. The
# blobs that survive are the ones a cached image lists, which includes
# every layer of an image the build produced; what goes is the build's
# private bookkeeping and the intermediates no image kept.
#
# Two properties make the sweep safe to advertise as "only garbage":
#
#   - it refuses to run while another command holds an exclusive lock.
#     A build in progress has written its COPY/ADD layers into the cache
#     but records them nowhere until the final manifest is stored, so
#     from the outside they are indistinguishable from orphans. Under
#     --build-cache the same refusal covers a sharper case: the steps
#     that build has already recorded name blobs the finished image
#     will list, and dropping the index unpins them mid-flight;
#   - a reference source it cannot parse aborts the whole sweep. An
#     unreadable reference is not an absent reference, and treating it
#     as one deletes the layers of an image the user still has. That
#     covers the build index too, for --orphan, which keeps what the
#     index pins; --build-cache does not consult it and so cannot be
#     stopped by it.

import os
import stat
import sys

from proot_distro.constants import (
    BASE_CACHE_DIR, LAYER_CACHE_DIR, PROGRAM_NAME,
)
from proot_distro.helpers.build_cache import (
    discard_index, index_path, recorded_layer_digests,
)
from proot_distro.helpers.docker import (
    layer_cache_path,
    referenced_blob_digests,
)
from proot_distro.locking import busy_locks
from proot_distro import dirfd, statedir
from proot_distro.message import (
    crit_error, log_info, log_error, quote_error, quote_path,
)
from proot_distro.progress import fmt_size


def _opendir_relaxing(dir_fd: int, name: str, st):
    """Open the subdirectory *name* under dir_fd. Descriptor, or None.

    A cache directory an interrupted write left unreadable is relaxed and
    retried, which is what lets the measuring pass see inside one -- the
    same bargain rmtree_at(force=True) makes when it comes to remove it.
    The chmod goes through dirfd.chmod_at (O_PATH|O_NOFOLLOW plus
    _chmod_fd), never through the name: Linux has no AT_SYMLINK_NOFOLLOW
    for fchmodat(2), so os.chmod() on a name a guest has re-pointed
    relaxes whatever host file it leads to.
    """
    try:
        return dirfd.opendir_at(dir_fd, name)
    except PermissionError:
        pass
    except OSError:
        return None
    dirfd.chmod_at(dir_fd, name, stat.S_IMODE(st.st_mode) | stat.S_IRWXU,
                   only_dir=True)
    try:
        return dirfd.opendir_at(dir_fd, name)
    except OSError:
        return None


def _relax_cache_root() -> None:
    """Add u+rwx to BASE_CACHE_DIR itself, so a sealed root can be emptied.

    The walk below relaxes every directory it cannot descend, but the
    root is the one it never meets — os.walk() used to hand it to
    _ensure_readable like any other. Done through the *parent's*
    descriptor, because os.chmod() on the name follows a symlink and
    fchmodat(2) has no AT_SYMLINK_NOFOLLOW; a root that is a symlink is
    simply left alone rather than relaxed through.
    """
    parent, name = os.path.split(BASE_CACHE_DIR.rstrip(os.sep))
    if not parent or not name:
        return
    try:
        parent_fd = dirfd.opendir(parent)
    except OSError:
        return
    try:
        try:
            st = dirfd.lstat_at(parent_fd, name)
        except OSError:
            return
        dirfd.chmod_at(parent_fd, name,
                       stat.S_IMODE(st.st_mode) | stat.S_IRWXU,
                       only_dir=True)
    finally:
        os.close(parent_fd)


def _open_cache_root() -> int:
    """Open BASE_CACHE_DIR as a descriptor. Raises what it cannot open.

    On Termux the cache lives *inside* RUNTIME_DIR, whose components are
    guest-writable -- $TERMUX_PREFIX is bound read-write into every
    non-isolated container -- so `cache` is one more name a session can
    leave behind as a symlink. os.path.isdir() answered "yes" for one and
    dirfd.opendir() then followed it, handing this command a host
    directory to empty. statedir walks down to the root with O_NOFOLLOW
    instead, and a component that is not a plain directory comes back as
    ENOTDIR, which the caller reports rather than descends.

    A cache root this user cannot enter is relaxed and retried, the same
    bargain the walk below makes for every directory under it.
    """
    try:
        return statedir.open_state_dir(BASE_CACHE_DIR)
    except PermissionError:
        _relax_cache_root()
        return statedir.open_state_dir(BASE_CACHE_DIR)


def _tree_size(root_fd: int) -> int:
    """Total bytes of the regular files under root_fd.

    os.walk() plus os.stat()/os.chmod() on each name was the shape of
    this, and every one of those calls follows a symlink. The cache is
    guest-writable -- on Termux it sits under the $TERMUX_PREFIX bound
    read-write into every non-isolated container -- so a planted
    `oci_layers/x -> ~/.bashrc` had its target stat'ed for the total and
    chmod'ed u+rw on the way past. Nothing needs relaxing to be measured
    or unlinked in the first place except a directory that cannot be
    descended, so that is the only chmod left, and it goes through a
    descriptor.

    Directories ride an explicit stack, in the layout dirfd's own walks
    use: how deep the tree goes is not this program's choice, and one
    past the interpreter's limit must not end the command in a
    RecursionError.
    """
    total = 0
    # Frame layout: [fd, None, pending names, owned].
    stack = [[root_fd, None, None, False]]
    try:
        while stack:
            frame = stack[-1]
            fd, _, pending, owned = frame
            if pending is None:
                try:
                    pending = frame[2] = dirfd.listdir_at(fd)
                except OSError:
                    pending = frame[2] = []
            if not pending:
                stack.pop()
                if owned:
                    os.close(fd)
                continue
            name = pending.pop()
            try:
                st = dirfd.lstat_at(fd, name)
            except OSError:
                continue
            if not stat.S_ISDIR(st.st_mode):
                if stat.S_ISREG(st.st_mode):
                    total += st.st_size
                continue
            sub = _opendir_relaxing(fd, name, st)
            if sub is not None:
                stack.append([sub, None, None, True])
    except BaseException:
        dirfd.close_frames(stack)
        raise
    return total


def command_clear_cache(args) -> None:
    """Empty BASE_CACHE_DIR, or sweep layers for --orphan/--build-cache."""
    drop_build_index = getattr(args, "build_cache", False)
    if drop_build_index or getattr(args, "orphan", False):
        # Passing both asks for the same work: --build-cache is the
        # orphan sweep with one root fewer, so it subsumes --orphan
        # rather than conflicting with it.
        _sweep_layers(args, drop_build_index)
        return

    verbose = getattr(args, "verbose", False)

    # Everything below the cache root is addressed as (dir_fd, name) from
    # here on, and the root itself is walked down to rather than named
    # (see _open_cache_root).
    try:
        root_fd = _open_cache_root()
    except FileNotFoundError:
        log_info("Cache is empty.")
        return
    except OSError as exc:
        crit_error(f"cannot read the cache directory "
                   f"'{quote_path(BASE_CACHE_DIR)}': {quote_error(exc)}")
        sys.exit(1)

    try:
        total = _tree_size(root_fd)
        try:
            names = dirfd.listdir_at(root_fd)
        except OSError as exc:
            crit_error(f"cannot read the cache directory "
                       f"'{quote_path(BASE_CACHE_DIR)}': {quote_error(exc)}")
            sys.exit(1)

        if not names:
            log_info("Cache is empty.")
            return

        log_info("Clearing cache...")

        # Through descriptors, and iteratively. On Termux this directory
        # sits under the bound $TERMUX_PREFIX, so a guest can build a tree
        # here deeper than the interpreter's stack — and shutil.rmtree()
        # answered that with a RecursionError, which an `except OSError`
        # does not catch. Reporting is the walk's own callbacks, which
        # name the entry that actually would not go rather than the
        # directory above it. rmtree_at covers a plain file at the top
        # level too, so there is one removal path rather than two.
        def _under(rel, top):
            return quote_path(os.path.join(top, rel) if rel else top)

        for name in names:
            top = os.path.join(BASE_CACHE_DIR, name)

            def _failed(rel, exc, top=top):
                log_error(f"Cannot remove '{_under(rel, top)}': "
                          f"{quote_error(exc)}")

            def _removed(rel, top=top):
                log_info(f"Removing: '{_under(rel, top)}'")

            dirfd.rmtree_at(
                root_fd, name, force=True, on_error=_failed,
                on_remove=_removed if verbose else None,
            )
    finally:
        os.close(root_fd)

    log_info(f"Reclaimed {fmt_size(total)} of disk space.")


# ---------------------------------------------------------------------------
# Layer sweep (--orphan / --build-cache)
# ---------------------------------------------------------------------------

def _referenced_blob_names(with_build_index: bool) -> set:
    """Return the layer-cache file names that are still referenced.

    The manifest cache is always a reference source; the build index is
    one unless the caller is about to delete it. Either source failing
    to answer ends the command rather than shrinking the set. Digests
    are mapped forward into file names, never the reverse: a name in the
    cache is garbage exactly when no live reference produces it.
    """
    digests, unreadable = referenced_blob_digests()
    if unreadable:
        crit_error(
            f"cached image entry '{quote_path(unreadable[0])}' cannot be "
            f"read, so the layers it holds cannot be identified. Nothing "
            f"was removed."
        )
        sys.exit(1)

    if with_build_index:
        build_digests, readable = recorded_layer_digests()
        if not readable:
            crit_error(
                f"the build cache index cannot be read, so the layers it "
                f"pins cannot be identified. Nothing was removed - use "
                f"'{PROGRAM_NAME} clear-cache --build-cache' to drop the "
                f"index and collect them."
            )
            sys.exit(1)
        digests |= build_digests

    names = set()
    for digest in digests:
        try:
            names.add(os.path.basename(layer_cache_path(digest)))
        except RuntimeError:
            # A digest too malformed to map to a path names no file in
            # the cache: every writer validates before creating one.
            continue
    return names


def _drop_build_index(verbose: bool) -> tuple:
    """Delete the build cache index, returning (removed, bytes reclaimed).

    Failure is fatal on purpose. The caller goes on to collect the
    layers this index was pinning, which is only correct once the
    entries naming them are gone.
    """
    try:
        removed, size = discard_index()
    except OSError as exc:
        crit_error(
            f"cannot remove the build cache index "
            f"'{quote_path(index_path())}': "
            f"{quote_error(exc)}. Nothing was removed."
        )
        sys.exit(1)

    if not removed:
        return False, 0
    if verbose:
        log_info(f"Removed: '{quote_path(index_path())}'")
    log_info(f"Removed the build cache index ({fmt_size(size)}).")
    return True, size


def _open_layer_cache():
    """Open LAYER_CACHE_DIR as a descriptor. None when it is not there.

    Every component below the trust root is walked with O_NOFOLLOW rather
    than named outright: the cache is guest-writable on Termux, and
    os.listdir() on a planted `oci_layers -> <host dir>` -- or on a
    planted `cache` one level above it -- would have handed the sweep a
    directory of host files to unlink. A component that is not a plain
    directory therefore surfaces as an error and stops the command, the
    same as any other unreadable layer cache -- an unreadable reference
    is not an absent one, and neither is an unreadable cache.
    """
    try:
        return statedir.open_state_dir(LAYER_CACHE_DIR)
    except FileNotFoundError:
        return None
    except OSError as exc:
        crit_error(f"cannot read the layer cache: {quote_error(exc)}")
        sys.exit(1)


def _collect_orphans(dir_fd, keep: set) -> tuple:
    """Return ([(name, size)], total_bytes) for every collectable blob."""
    if dir_fd is None:
        return [], 0
    try:
        names = dirfd.listdir_at(dir_fd)
    except OSError as exc:
        crit_error(f"cannot read the layer cache: "
                   f"{quote_error(exc)}")
        sys.exit(1)

    orphans = []
    total = 0
    for name in names:
        if name in keep:
            continue
        try:
            st = dirfd.lstat_at(dir_fd, name)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            # Nothing writes a directory or a symlink here; leaving one
            # alone costs nothing and keeps the sweep to one file type.
            continue
        orphans.append((name, st.st_size))
        total += st.st_size
    return orphans, total


def _sweep_layers(args, drop_build_index: bool) -> None:
    """Delete the layer blobs nothing references, and optionally the index."""
    verbose = getattr(args, "verbose", False)

    busy = busy_locks()
    if busy:
        why = (
            "A build in progress has recorded steps whose layers this "
            "would unpin, and it names them in the image it stores last "
            "of all"
            if drop_build_index else
            "A build or install in progress writes layers that nothing "
            "references yet, so they cannot be told apart from orphans"
        )
        crit_error(
            f"another {PROGRAM_NAME} command is running{busy[0][1]}. "
            f"{why}; try again once it has finished."
        )
        sys.exit(1)

    # Computed before anything is deleted, so a reference source that
    # cannot be read leaves the cache exactly as it was.
    keep = _referenced_blob_names(with_build_index=not drop_build_index)

    index_removed, reclaimed = False, 0
    if drop_build_index:
        index_removed, reclaimed = _drop_build_index(verbose)

    layer_fd = _open_layer_cache()
    try:
        _remove_orphans(layer_fd, keep, verbose, drop_build_index,
                        index_removed, reclaimed)
    finally:
        if layer_fd is not None:
            os.close(layer_fd)


def _remove_orphans(layer_fd, keep, verbose, drop_build_index,
                    index_removed, reclaimed) -> None:
    """Unlink every collectable blob and report what was reclaimed."""
    orphans, total = _collect_orphans(layer_fd, keep)

    if orphans:
        log_info(f"Removing {len(orphans)} orphan layer(s) "
                 f"({fmt_size(total)})...")
    elif not drop_build_index:
        log_info("No orphan layers found.")
        return
    elif not index_removed:
        # Nothing was there and nothing was collectable; the reclaimed
        # line below would only report zero. Note this turns on whether
        # the index existed, not on its size: a stat that failed while
        # the unlink succeeded still removed something.
        log_info("The build cache is already empty.")
        return

    failed = False
    for name, size in orphans:
        path = os.path.join(LAYER_CACHE_DIR, name)
        try:
            os.unlink(name, dir_fd=layer_fd)
        except OSError as exc:
            log_error(f"Cannot remove '{quote_path(path)}': "
                      f"{quote_error(exc)}")
            failed = True
            continue
        reclaimed += size
        if verbose:
            log_info(f"Removed: '{quote_path(path)}'")

    log_info(f"Reclaimed {fmt_size(reclaimed)} of disk space.")

    if failed:
        log_error("Finished with errors. Some files probably were not "
                  "deleted.")
        sys.exit(1)
