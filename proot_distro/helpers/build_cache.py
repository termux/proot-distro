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

# Architecture: Tiny on-disk index keyed by a recipe hash that the
# build engine computes per instruction. A hit means "we have a
# pre-built layer that matches this exact (parent + instruction +
# inputs) combination — apply the cached blob instead of re-executing
# the instruction." Stored under BASE_CACHE_DIR alongside layers/ and
# manifests/ so a single `clear-cache` removes all build artefacts
# together; `clear-cache --build-cache` drops this index alone and lets
# the layer sweep collect what it was pinning.

import contextlib
import fcntl
import hashlib
import json
import os
import time

from proot_distro import dirfd, statedir
from proot_distro.atomic import atomic_write
from proot_distro.constants import BASE_CACHE_DIR
from proot_distro.locking import open_lock_file_at


_INDEX_PATH = os.path.join(BASE_CACHE_DIR, "build_cache_index.json")
_INDEX_LOCK_PATH = _INDEX_PATH + ".lock"


def _open_lock_fd():
    """Open the index's lock file. Descriptor, or None to proceed unlocked.

    Both halves used to name the file: os.makedirs() on the cache
    directory, which accepts a symlink to a directory, and then
    os.open(O_RDWR | O_CREAT) on the lock file itself, which follows one.
    The cache is guest-writable on Termux -- it sits under the
    $TERMUX_PREFIX bound read-write into every non-isolated container --
    and this name is as predictable as they come, so a planted
    `build_cache_index.json.lock -> <host path>` had this program create
    that file, or open an existing one and hold a lock on it. A FIFO under
    the same name was worse: the open blocked until a peer appeared, which
    a hostile guest simply never provides, and the build stopped there.

    The directory is walked down to with O_NOFOLLOW and the entry opened
    through locking.open_lock_file_at(), which refuses anything that is
    not a plain file and replaces it -- nothing but this module writes
    here.
    """
    try:
        dir_fd, name = statedir.open_state_parent(_INDEX_LOCK_PATH,
                                                  create=True)
    except OSError:
        return None
    try:
        return open_lock_file_at(dir_fd, name, _INDEX_LOCK_PATH)
    finally:
        os.close(dir_fd)


@contextlib.contextmanager
def _index_lock():
    """Hold an exclusive flock on the index for the read-modify-write cycle.

    The index is a single JSON file shared across all builds, so two
    concurrent `record()` calls would otherwise read-modify-write
    independently and the last writer would silently drop the other's
    entry. The flock serialises updates; on filesystems that don't
    support flock -- or where the lock file cannot be created at all --
    the call proceeds unlocked (last-writer-wins, same behaviour as
    before).
    """
    fd = _open_lock_fd()
    if fd is None:
        yield
        return
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
        except OSError:
            pass  # Filesystem ignores flock; proceed unlocked.
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass
        os.close(fd)


def _read_index() -> bytes:
    """Return the index file's raw content.

    Named as (directory fd, entry) rather than as a path, for the reason
    the lock file is: open(_INDEX_PATH) followed a planted symlink -- it
    could only ever read, since every write goes through atomic_replace(),
    but a FIFO left under the name blocked the read until a peer came,
    and none does. open_regular_at() refuses every type but a regular
    file.

    FileNotFoundError means there is no index, which each caller reads
    differently: a fresh one for lookup(), "the index pins nothing" for
    the layer sweep. Anything else is a read failure and must not pass
    for either -- an index too large to read included, which is why the
    capped read (statedir.read_state_file) raises rather than truncating:
    a half-read index parses as no index at all, and the layer sweep
    would then collect every blob the real one pins. The cache is
    guest-writable on Termux, so how many bytes stand under this name is
    not this program's choice.
    """
    dir_fd, name = statedir.open_state_parent(_INDEX_PATH)
    try:
        fd, _st = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    finally:
        os.close(dir_fd)
    try:
        return statedir.read_state_file(fd)
    finally:
        os.close(fd)


def _load_index():
    try:
        data = json.loads(_read_index())
    except (OSError, ValueError):
        return {"version": 1, "entries": {}}
    if not isinstance(data, dict):
        return {"version": 1, "entries": {}}
    data.setdefault("version", 1)
    data.setdefault("entries", {})
    if not isinstance(data["entries"], dict):
        data["entries"] = {}
    return data


def _save_index(data):
    with atomic_write(_INDEX_PATH, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)


def lookup(recipe_hash):
    """Return the cache entry dict for `recipe_hash`, or None."""
    if not recipe_hash:
        return None
    data = _load_index()
    return data.get("entries", {}).get(recipe_hash)


def recorded_layer_digests():
    """Return (digests, readable) for every layer blob the index pins.

    A cache hit applies its layer straight out of the layer cache, so
    those blobs are live references even when no image manifest lists
    them — a multi-stage intermediate, or a step whose image has since
    been rebuilt under another tag. Anything pruning the layer cache has
    to count them, or the first `build` after the prune re-runs every
    instruction.

    *readable* is False when the index exists but could not be parsed,
    which the caller must not read as "the index pins nothing". No lock
    is taken: _save_index() publishes through atomic_replace(), so a
    reader either sees the previous index or the next one, never a torn
    one — the same reason lookup() runs unlocked.
    """
    try:
        data = json.loads(_read_index())
    except FileNotFoundError:
        return set(), True
    except (OSError, ValueError):
        return set(), False

    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        return set(), False

    digests = set()
    for entry in entries.values():
        if isinstance(entry, dict) and entry.get("layer_digest"):
            digests.add(entry["layer_digest"])
    return digests, True


def index_path():
    """Return the on-disk location of the build-cache index."""
    return _INDEX_PATH


def discard_index():
    """Delete the index, returning (removed, the bytes it occupied).

    *removed* is False when there was nothing there to delete. An
    OSError is deliberately left to propagate: a caller dropping the
    index in order to collect the layers it pinned must not go on to
    delete them while the entries naming them are still on disk.

    No lock is taken. record() serialises the read-modify-write cycle it
    performs, but an unlink is not one — a concurrent record() either
    wrote before it and loses its entry, which is the point of the call,
    or writes afterwards and starts a fresh index. Neither outcome is a
    torn file, the same reason lookup() and recorded_layer_digests()
    read unlocked.
    """
    try:
        dir_fd, name = statedir.open_state_parent(_INDEX_PATH)
    except FileNotFoundError:
        return False, 0
    try:
        try:
            size = dirfd.lstat_at(dir_fd, name).st_size
        except FileNotFoundError:
            return False, 0
        except OSError:
            # Present but not stat'able; the unlink below is what decides
            # the outcome, and the size is only the report.
            size = 0
        try:
            os.unlink(name, dir_fd=dir_fd)
        except FileNotFoundError:
            return False, 0
    finally:
        os.close(dir_fd)
    return True, size


def record(recipe_hash, layer_digest, diff_id, size, image_config_patch=None):
    """Record a build-cache entry."""
    # Lock around the full read-modify-write so concurrent builds don't
    # clobber each other's records.
    with _index_lock():
        data = _load_index()
        entries = data.setdefault("entries", {})
        entries[recipe_hash] = {
            "layer_digest": layer_digest,
            "diff_id": diff_id,
            "size": size,
            "image_config_patch": image_config_patch or {},
            "created": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _save_index(data)


# ---------------------------------------------------------------------------
# Recipe-hash construction
# ---------------------------------------------------------------------------

def _canonical_value(value):
    if isinstance(value, list):
        return json.dumps(value, separators=(",", ":"))
    return str(value)


def _canonical_flags(flags):
    if not flags:
        return ""
    return "&".join(f"{k}={v}" for k, v in sorted(flags.items()))


def compute_recipe_hash(parent_layer_digest, instr, extra_inputs=""):
    """Compute the recipe hash for `instr` chained onto `parent_layer_digest`.

    `extra_inputs` is an opaque string that the caller appends to
    capture inputs the instruction itself doesn't carry (e.g. the
    digests of files referenced by COPY/ADD, or the relevant
    env+ARG state visible to a RUN).
    """
    h = hashlib.sha256()
    h.update((parent_layer_digest or "").encode())
    h.update(b"\x00")
    h.update(instr["name"].encode())
    h.update(b"\x00")
    h.update(_canonical_flags(instr.get("flags", {})).encode())
    h.update(b"\x00")
    h.update(_canonical_value(instr.get("value", "")).encode())
    h.update(b"\x00")
    for hd in instr.get("heredocs", []) or []:
        h.update(b"<<")
        h.update((hd.get("body") or "").encode())
        h.update(b">>")
    h.update(b"\x00")
    if isinstance(extra_inputs, bytes):
        h.update(extra_inputs)
    else:
        h.update(str(extra_inputs).encode())
    return h.hexdigest()
