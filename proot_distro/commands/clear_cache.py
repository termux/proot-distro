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
import shutil
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
from proot_distro.message import crit_error, log_info, log_error, quote_path
from proot_distro.progress import fmt_size


def _ensure_readable(path: str) -> None:
    """Attempt to add read/execute permissions to a directory entry."""
    try:
        st = os.stat(path)
        if os.path.isdir(path):
            os.chmod(path, st.st_mode | stat.S_IRWXU)
        else:
            os.chmod(path, st.st_mode | stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass


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

    if not os.path.isdir(BASE_CACHE_DIR):
        log_info("Cache is empty.")
        return

    total = 0
    for dirpath, _dirs, filenames in os.walk(BASE_CACHE_DIR):
        _ensure_readable(dirpath)
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            _ensure_readable(fpath)
            try:
                total += os.path.getsize(fpath)
            except OSError:
                pass

    if total == 0 and not any(True for _ in os.scandir(BASE_CACHE_DIR)):
        log_info("Cache is empty.")
        return

    log_info("Clearing cache...")

    for entry in os.scandir(BASE_CACHE_DIR):
        try:
            if entry.is_dir(follow_symlinks=False):
                if verbose:
                    for dirpath, _dirs, filenames in os.walk(entry.path):
                        for fname in filenames:
                            log_info(
                                f"Removing: '{os.path.join(dirpath, fname)}'"
                            )
                shutil.rmtree(entry.path)
            else:
                if verbose:
                    log_info(f"Removing: '{entry.path}'")
                os.remove(entry.path)
        except OSError as exc:
            log_error(f"Cannot remove '{entry.path}': {exc}")

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
            f"{quote_path(exc.strerror or str(exc))}. Nothing was removed."
        )
        sys.exit(1)

    if not removed:
        return False, 0
    if verbose:
        log_info(f"Removed: '{quote_path(index_path())}'")
    log_info(f"Removed the build cache index ({fmt_size(size)}).")
    return True, size


def _collect_orphans(keep: set) -> tuple:
    """Return ([(path, size)], total_bytes) for every collectable blob."""
    try:
        names = sorted(os.listdir(LAYER_CACHE_DIR))
    except FileNotFoundError:
        names = []
    except OSError as exc:
        crit_error(f"cannot read the layer cache: "
                   f"{quote_path(exc.strerror or str(exc))}")
        sys.exit(1)

    orphans = []
    total = 0
    for name in names:
        if name in keep:
            continue
        path = os.path.join(LAYER_CACHE_DIR, name)
        try:
            st = os.lstat(path)
        except OSError:
            continue
        if not stat.S_ISREG(st.st_mode):
            # Nothing writes a directory or a symlink here; leaving one
            # alone costs nothing and keeps the sweep to one file type.
            continue
        orphans.append((path, st.st_size))
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

    orphans, total = _collect_orphans(keep)

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
    for path, size in orphans:
        try:
            os.remove(path)
        except OSError as exc:
            log_error(f"Cannot remove '{quote_path(path)}': "
                      f"{quote_path(exc.strerror or str(exc))}")
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
