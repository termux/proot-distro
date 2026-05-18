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
# the instruction." Stored under DOWNLOAD_CACHE_DIR/buildcache/
# alongside layers/ and manifests/ so a single `clear-cache` removes
# all build artefacts together.

import hashlib
import json
import os
import time

from proot_distro.constants import DOWNLOAD_CACHE_DIR


_INDEX_PATH = os.path.join(DOWNLOAD_CACHE_DIR, "buildcache", "index.json")


def _load_index():
    try:
        with open(_INDEX_PATH) as fh:
            data = json.load(fh)
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
    os.makedirs(os.path.dirname(_INDEX_PATH), exist_ok=True)
    tmp = _INDEX_PATH + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
    os.replace(tmp, _INDEX_PATH)


def lookup(recipe_hash):
    """Return the cache entry dict for `recipe_hash`, or None."""
    if not recipe_hash:
        return None
    data = _load_index()
    return data.get("entries", {}).get(recipe_hash)


def record(recipe_hash, layer_digest, diff_id, size, image_config_patch=None):
    """Record a build-cache entry."""
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
