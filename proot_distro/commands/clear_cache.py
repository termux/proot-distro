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
#   default   — the entire download cache directory contents. Failures on
#               individual entries are reported but do not abort the
#               operation, so partial caches are still cleaned as much as
#               possible.
#   --orphan  — only the layer blobs nothing references any more. Every
#               other file, including the manifest cache and the build
#               index, is left exactly as it was.
#
# A blob is referenced when a cached image names it (manifest cache) or
# when a build-cache entry pins it (build index). The second half matters:
# a multi-stage intermediate, or a step whose image has since been
# rebuilt under a different tag, appears in no manifest at all, and
# collecting it would silently empty the build cache — which is what a
# plain `clear-cache` is for. Installed containers are deliberately not
# roots: their rootfs is an independent copy, the same reasoning
# `remove --image` already applies.
#
# Two properties make the sweep safe to advertise as "only garbage":
#
#   - it refuses to run while another command holds an exclusive lock.
#     A build in progress has written its COPY/ADD layers into the cache
#     but records them nowhere until the final manifest is stored, so
#     from the outside they are indistinguishable from orphans;
#   - a manifest entry or build index it cannot parse aborts the whole
#     sweep. An unreadable reference is not an absent reference, and
#     treating it as one deletes the layers of an image the user still
#     has.

import os
import shutil
import stat
import sys

from proot_distro.constants import (
    BASE_CACHE_DIR, LAYER_CACHE_DIR, PROGRAM_NAME,
)
from proot_distro.helpers.build_cache import recorded_layer_digests
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
    """Empty BASE_CACHE_DIR, or collect orphan layers with --orphan."""
    if getattr(args, "orphan", False):
        _clear_orphan_layers(args)
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
# Orphan layers
# ---------------------------------------------------------------------------

def _referenced_blob_names() -> set:
    """Return the layer-cache file names that are still referenced.

    Both reference sources are consulted — the manifest cache and the
    build index — and either failing to answer ends the command rather
    than shrinking the set. Digests are mapped forward into file names,
    never the reverse: a name in the cache is garbage exactly when no
    live reference produces it.
    """
    digests, unreadable = referenced_blob_digests()
    if unreadable:
        crit_error(
            f"cached image entry '{quote_path(unreadable[0])}' cannot be "
            f"read, so the layers it holds cannot be identified. Nothing "
            f"was removed."
        )
        sys.exit(1)

    build_digests, readable = recorded_layer_digests()
    if not readable:
        crit_error(
            f"the build cache index cannot be read, so the layers it pins "
            f"cannot be identified. Nothing was removed - use "
            f"'{PROGRAM_NAME} clear-cache' to empty the whole cache."
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


def _clear_orphan_layers(args) -> None:
    """Delete the layer blobs no image manifest and no build entry holds."""
    verbose = getattr(args, "verbose", False)

    busy = busy_locks()
    if busy:
        crit_error(
            f"another {PROGRAM_NAME} command is running{busy[0][1]}. A "
            f"build or install in progress writes layers that nothing "
            f"references yet, so they cannot be told apart from orphans; "
            f"try again once it has finished."
        )
        sys.exit(1)

    keep = _referenced_blob_names()

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

    if not orphans:
        log_info("No orphan layers found.")
        return

    log_info(f"Removing {len(orphans)} orphan layer(s) "
             f"({fmt_size(total)})...")

    failed = False
    reclaimed = 0
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
