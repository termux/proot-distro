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

# Architecture: On-disk caches that let pulls and builds run offline.
# Two caches live side-by-side under BASE_CACHE_DIR:
#
#   layers/<digest-with-colon-as-underscore>
#       One file per blob. Cached layers are trusted (their content
#       digest was verified on entry via the streaming sha256).
#
#   manifests/<sha256-prefix>.json
#       { "manifest": ..., "repo": ..., "image_config": ... }
#       Key is the first 16 hex chars of sha256("<canonical_ref>_<arch>").

import hashlib
import json
import os

from proot_distro.atomic import atomic_replace
from proot_distro.constants import LAYER_CACHE_DIR, MANIFEST_CACHE_DIR
from proot_distro.helpers.docker.refs import parse_image_ref


def layer_cache_path(digest: str) -> str:
    """Return the on-disk path of the cached blob for *digest*."""
    return os.path.join(LAYER_CACHE_DIR, digest.replace(":", "_"))


def manifest_cache_path(image_ref: str, arch: str) -> str:
    """Return the manifest-cache path for (*image_ref*, *arch*)."""
    registry, repo, tag = parse_image_ref(image_ref)
    canonical = f"{registry + '/' if registry else ''}{repo}:{tag}_{arch}"
    key = hashlib.sha256(canonical.encode()).hexdigest()[:16]
    return os.path.join(MANIFEST_CACHE_DIR, key + ".json")


def save_manifest_cache(
    image_ref: str, arch: str,
    manifest: dict, repo: str, image_config: dict,
) -> None:
    """Persist a manifest + image-config pair under the canonical cache key."""
    payload = {"manifest": manifest, "repo": repo, "image_config": image_config}
    with atomic_replace(manifest_cache_path(image_ref, arch)) as tmp:
        with open(tmp, "w") as fh:
            json.dump(payload, fh)


def load_manifest_cache(image_ref: str, arch: str):
    """Return (manifest, repo, image_config) from cache.

    On a cache miss (or read/parse error) returns ``(None, None, {})`` —
    callers check ``manifest is None`` to detect the miss.
    """
    try:
        with open(manifest_cache_path(image_ref, arch)) as fh:
            data = json.load(fh)
        return data["manifest"], data["repo"], data.get("image_config", {})
    except (OSError, json.JSONDecodeError, KeyError):
        return None, None, {}


def all_layers_cached(layers: list) -> bool:
    """Return True iff every layer's blob file is already on disk."""
    return all(
        os.path.isfile(layer_cache_path(layer["digest"])) for layer in layers
    )
