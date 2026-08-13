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
#       { "image_ref": ..., "arch": ...,
#         "manifest": ..., "repo": ..., "image_config": ... }
#       Key is the first 16 hex chars of sha256("<canonical_ref>_<arch>").
#
# A manifest-cache entry plus its layer blobs is what the user-facing
# commands call an *image*: the unit `install <ref>` resolves offline,
# `push <ref>` uploads, and `list --image` / `remove --image` manage.
# Because the key is a hash, the entry itself has to carry the
# reference and architecture that produced it — iter_cached_images()
# reads them back. Entries written before that field existed are
# repaired in place by annotate_manifest_cache() on the next pull, and
# named on a best-effort basis from installed containers meanwhile.

import hashlib
import json
import os
import re

from proot_distro.atomic import atomic_replace
from proot_distro.constants import (
    CONTAINERS_DIR, LAYER_CACHE_DIR, MANIFEST_CACHE_DIR,
)
from proot_distro.helpers.docker.refs import DOCKER_TO_ARCH, canonical_ref


# OCI digest grammar (algorithm ":" encoded). The algorithm component
# allows alphanumerics joined by single +, _, -, or . separators, so
# bare ".." can never appear in a valid digest. Anchored so a crafted
# string like "../foo:bar" — which would make layer_cache_path or any
# digest→path mapper escape LAYER_CACHE_DIR — is rejected.
_DIGEST_RE = re.compile(
    r"^[A-Za-z0-9]+(?:[+_.\-][A-Za-z0-9]+)*:[A-Fa-f0-9]+$"
)


def validate_digest(digest: str) -> str:
    """Return *digest* unchanged when well-formed; raise otherwise.

    Used as a choke point before any conversion of an untrusted digest
    into a filesystem path (layer cache, OCI blob layout). Accepts any
    OCI-conformant algorithm/hex pair; rejects anything containing path
    separators or empty/dot components.
    """
    if not isinstance(digest, str) or not _DIGEST_RE.match(digest):
        raise RuntimeError(f"Malformed digest: {digest!r}")
    return digest


def layer_cache_path(digest: str) -> str:
    """Return the on-disk path of the cached blob for *digest*.

    Refuses malformed digests so callers cannot accidentally route a
    crafted value past LAYER_CACHE_DIR via path traversal.
    """
    validate_digest(digest)
    return os.path.join(LAYER_CACHE_DIR, digest.replace(":", "_"))


def manifest_cache_path(image_ref: str, arch: str) -> str:
    """Return the manifest-cache path for (*image_ref*, *arch*)."""
    key = hashlib.sha256(
        f"{canonical_ref(image_ref)}_{arch}".encode()
    ).hexdigest()[:16]
    return os.path.join(MANIFEST_CACHE_DIR, key + ".json")


def save_manifest_cache(
    image_ref: str, arch: str,
    manifest: dict, repo: str, image_config: dict,
) -> str:
    """Persist a manifest + image-config pair under the canonical cache key.

    The reference and architecture are stored alongside the payload:
    the file name is a hash, so they are the only record of which image
    the entry describes. Returns the path written.
    """
    payload = {
        "image_ref": image_ref,
        "arch": arch,
        "manifest": manifest,
        "repo": repo,
        "image_config": image_config,
    }
    path = manifest_cache_path(image_ref, arch)
    with atomic_replace(path) as tmp:
        with open(tmp, "w") as fh:
            json.dump(payload, fh)
    return path


def annotate_manifest_cache(image_ref: str, arch: str) -> None:
    """Backfill ref/arch metadata onto an entry written by an older version.

    Called on a manifest-cache hit, where both values are known for
    certain. Entries that already carry them (and entries that cannot be
    read or rewritten) are left untouched, so this costs one small JSON
    read per cached pull and never invalidates anything.
    """
    path = manifest_cache_path(image_ref, arch)
    try:
        with open(path) as fh:
            payload = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return
    if not isinstance(payload, dict) or "manifest" not in payload:
        return
    if payload.get("image_ref") and payload.get("arch"):
        return
    payload["image_ref"] = image_ref
    payload["arch"] = arch
    try:
        with atomic_replace(path) as tmp:
            with open(tmp, "w") as fh:
                json.dump(payload, fh)
    except OSError:
        pass


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


# ---------------------------------------------------------------------------
# Image inventory — what `list --image` and `remove --image` operate on
# ---------------------------------------------------------------------------

def _blob_size(digest: str):
    """Return the on-disk size of a layer blob, or None when unavailable."""
    try:
        return os.path.getsize(layer_cache_path(digest))
    except (OSError, RuntimeError):
        # Missing blob, or a digest too malformed to map to a path at
        # all — either way this layer occupies no cache space.
        return None


def _read_record(path: str, image_ref: str = "", arch: str = ""):
    """Build the cached-image record for one manifest-cache file.

    *image_ref* / *arch* are fallbacks used when the entry predates
    those fields (see the module header). Returns None when the file
    isn't a readable manifest-cache entry.
    """
    try:
        with open(path) as fh:
            payload = json.load(fh)
        manifest = payload["manifest"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError):
        return None
    if not isinstance(payload, dict) or not isinstance(manifest, dict):
        return None

    image_config = payload.get("image_config")
    if not isinstance(image_config, dict):
        image_config = {}

    layers = [
        layer for layer in manifest.get("layers") or []
        if isinstance(layer, dict) and layer.get("digest")
    ]
    size = 0
    missing = 0
    for layer in layers:
        blob_size = _blob_size(layer["digest"])
        if blob_size is None:
            missing += 1
        else:
            size += blob_size

    try:
        cached_at = os.path.getmtime(path)
    except OSError:
        cached_at = 0.0

    return {
        "path": path,
        "key": os.path.splitext(os.path.basename(path))[0],
        "image_ref": payload.get("image_ref") or image_ref or "",
        "repo": payload.get("repo") or "",
        "arch": (
            payload.get("arch") or arch
            or DOCKER_TO_ARCH.get(image_config.get("architecture", ""), "")
        ),
        "image_id": (
            (manifest.get("config") or {}).get("digest", "").split(":")[-1]
        ),
        "layers": layers,
        "size": size,
        "missing": missing,
        "created": image_config.get("created") or "",
        "cached_at": cached_at,
    }


def referenced_blob_digests():
    """Return (digests, unreadable) covering every cached image's blobs.

    *digests* is every blob digest the manifest cache names — the layers
    and the config descriptor, i.e. the complete set of blobs an entry
    would need to be installed or pushed. *unreadable* lists the entry
    paths that could not be parsed.

    Callers pruning the layer cache must treat a non-empty *unreadable*
    as a reason to stop rather than as an absence of references, which
    is why this exists next to iter_cached_images() instead of being
    derived from it: that function skips an entry it cannot read, which
    is right for an inventory and wrong for deciding what is garbage.
    """
    digests, unreadable = set(), []
    try:
        names = sorted(os.listdir(MANIFEST_CACHE_DIR))
    except FileNotFoundError:
        return digests, unreadable
    except OSError:
        # The directory itself is unreadable: every entry in it is
        # unaccounted for, so report the directory as the blocker.
        return digests, [MANIFEST_CACHE_DIR]

    for fname in names:
        # Entries are '<key>.json'; atomic_replace's in-flight temporary
        # files carry a '.tmp' suffix and are deliberately not read.
        if not fname.endswith(".json"):
            continue
        path = os.path.join(MANIFEST_CACHE_DIR, fname)
        try:
            with open(path) as fh:
                payload = json.load(fh)
        except (OSError, json.JSONDecodeError):
            unreadable.append(path)
            continue
        manifest = (
            payload.get("manifest") if isinstance(payload, dict) else None
        )
        if not isinstance(manifest, dict):
            unreadable.append(path)
            continue
        descriptors = list(manifest.get("layers") or [])
        descriptors.append(manifest.get("config"))
        for descriptor in descriptors:
            if isinstance(descriptor, dict) and descriptor.get("digest"):
                digests.add(descriptor["digest"])
    return digests, unreadable


def _ref_hints() -> dict:
    """Map cache key → (image_ref, arch) recovered from installed containers.

    Only used to name entries that predate the stored metadata: a
    container's manifest.json records the reference and architecture it
    was installed from, and the cache key is a pure function of those
    two, so any still-installed container identifies its own image.
    """
    from proot_distro.paths import container_manifest

    hints = {}
    try:
        names = sorted(os.listdir(CONTAINERS_DIR))
    except OSError:
        return hints
    for name in names:
        try:
            with open(container_manifest(name)) as fh:
                data = json.load(fh)
        except (OSError, json.JSONDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        ref, arch = data.get("image_ref"), data.get("arch")
        if not ref or not arch:
            continue
        key = os.path.splitext(
            os.path.basename(manifest_cache_path(ref, arch))
        )[0]
        hints.setdefault(key, (ref, arch))
    return hints


def image_cache_entry(image_ref: str, arch: str):
    """Return the cached-image record for (*image_ref*, *arch*), or None.

    Resolution goes through the cache key, so it finds legacy entries
    that carry no reference of their own just as reliably.
    """
    path = manifest_cache_path(image_ref, arch)
    if not os.path.isfile(path):
        return None
    return _read_record(path, image_ref=canonical_ref(image_ref), arch=arch)


def iter_cached_images() -> list:
    """Return one record per cached image, sorted by reference then arch.

    Unreadable entries are skipped. Records whose reference could not be
    determined sort last and carry an empty 'image_ref'.
    """
    try:
        names = sorted(os.listdir(MANIFEST_CACHE_DIR))
    except OSError:
        return []

    hints = None
    records = []
    for fname in names:
        if not fname.endswith(".json"):
            continue
        record = _read_record(os.path.join(MANIFEST_CACHE_DIR, fname))
        if record is None:
            continue
        if not record["image_ref"]:
            if hints is None:
                hints = _ref_hints()
            ref, arch = hints.get(record["key"], ("", ""))
            record["image_ref"] = ref
            record["arch"] = record["arch"] or arch
        records.append(record)

    records.sort(
        key=lambda r: (not r["image_ref"], r["image_ref"], r["arch"], r["key"])
    )
    return records
