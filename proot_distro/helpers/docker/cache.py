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
#       One file per blob, named for the digest of its content. The
#       name is not evidence: download_blob() verifies a blob it
#       fetches, but the directory itself is writable by anything that
#       can reach it — on Termux it sits under the $TERMUX_PREFIX bound
#       into every non-isolated container, and `install <archive>`
#       deposits blobs a remote party chose the digests for. Every
#       consumer therefore re-hashes a blob before using it, via
#       open_verified_layer() (re-obtainable: evict and refetch) or
#       open_required_layer() (locally built: refuse, keep the file).
#       Both hand back an open **descriptor**, not a path: hashing a
#       name and then reading that name again are two acts on two
#       possibly-different files, and a guest sharing the directory can
#       swap the blob in between.
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
import stat

from proot_distro.atomic import atomic_replace
from proot_distro.constants import (
    CONTAINERS_DIR, LAYER_CACHE_DIR, MANIFEST_CACHE_DIR,
)
from proot_distro.message import warn
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
    """Return True iff every layer's blob file is already on disk.

    Presence only — see open_verified_layer() for the content check a
    caller must make before it uses a blob for anything.
    """
    return all(
        os.path.isfile(layer_cache_path(layer["digest"])) for layer in layers
    )


# ---------------------------------------------------------------------------
# Blob integrity
# ---------------------------------------------------------------------------

# Hash in 1 MiB slices: large enough that the read syscalls disappear
# against the hashing itself, small enough not to matter on a phone.
_BLOB_CHUNK = 1024 * 1024


def split_digest(digest: str) -> tuple:
    """Return ``(algorithm, lowercase hex)`` for a hashable *digest*.

    Raises RuntimeError for a malformed digest and for any algorithm
    this program cannot compute. Refusing the latter is the point: a
    digest we cannot hash is a digest we cannot check, so no blob may
    be used under one.
    """
    validate_digest(digest)
    algo, hex_val = digest.split(":", 1)
    if algo.lower() != "sha256":
        raise RuntimeError(
            f"Unsupported digest algorithm '{algo}' in '{digest}' "
            f"(only sha256 is supported)."
        )
    return algo.lower(), hex_val.lower()


def data_matches_digest(data: bytes, digest: str) -> bool:
    """Return True when *data* hashes to *digest*."""
    _algo, expected = split_digest(digest)
    return hashlib.sha256(data).hexdigest() == expected


def require_data_digest(data: bytes, digest: str, what: str = "Blob") -> bytes:
    """Return *data* when it hashes to *digest*; raise otherwise.

    For content addressed by digest but not stored in the layer cache —
    a manifest fetched by digest out of an index, an image config blob,
    a JSON blob read out of an OCI archive.
    """
    if not data_matches_digest(data, digest):
        raise RuntimeError(
            f"{what} does not match its digest {digest} "
            f"(got sha256:{hashlib.sha256(data).hexdigest()}). "
            f"The content was altered in transit or at rest."
        )
    return data


def file_matches_digest(path: str, digest: str) -> bool:
    """Return True when the file at *path* hashes to *digest*.

    An unreadable or vanished file answers False — the caller wanted a
    usable blob, and one it cannot read is not usable. A digest whose
    algorithm cannot be hashed still raises, because that is a bug or an
    attack rather than a missing file.
    """
    _algo, expected = split_digest(digest)
    hasher = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while True:
                chunk = fh.read(_BLOB_CHUNK)
                if not chunk:
                    break
                hasher.update(chunk)
    except OSError:
        return False
    return hasher.hexdigest() == expected


def fd_matches_digest(fd: int, digest: str) -> bool:
    """True when the bytes behind *fd* hash to *digest*.

    The position is rewound before and after, so the caller can hand the
    same descriptor straight to whoever reads it.
    """
    _algo, expected = split_digest(digest)
    hasher = hashlib.sha256()
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        while True:
            chunk = os.read(fd, _BLOB_CHUNK)
            if not chunk:
                break
            hasher.update(chunk)
        os.lseek(fd, 0, os.SEEK_SET)
    except OSError:
        return False
    return hasher.hexdigest() == expected


def _open_blob(digest: str):
    """Open the cache file for *digest* read-only. Descriptor, or None.

    O_NOFOLLOW, and a regular file or nothing: this directory is not
    only ours to write (see the module header), so the entry standing
    at a blob's name may be a symlink or a pipe someone else put there.
    """
    try:
        fd = os.open(layer_cache_path(digest), os.O_RDONLY | os.O_NOFOLLOW
                     | os.O_NONBLOCK)
    except OSError:
        return None
    try:
        if not stat.S_ISREG(os.fstat(fd).st_mode):
            os.close(fd)
            return None
    except OSError:
        os.close(fd)
        return None
    return fd


def open_verified_layer(digest: str, *, evict: bool = True):
    """Open the cached blob for *digest* if it matches. Descriptor, or None.

    None means "do not use the cache for this layer": either no usable
    blob is there, or the one that is does not hash to its own name. In
    the second case the file is removed (unless *evict* is False) so the
    next attempt refetches it, and the user is told — a blob whose
    content stopped matching its digest was replaced by something, and
    silently repairing that would hide it.

    A **descriptor** rather than a path, because hashing a name and then
    reading that name again are two acts on two possibly-different
    files. The window is not theoretical: on Termux LAYER_CACHE_DIR sits
    under the `$TERMUX_PREFIX` bound read-write into every non-isolated
    container, so a guest running while an install proceeds can swap the
    blob between the check and the read. The caller closes it.

    Use this wherever the blob can be obtained again (a registry pull, a
    build-cache hit, an archive still open); use open_required_layer()
    where it cannot.
    """
    fd = _open_blob(digest)
    if fd is None:
        return None
    if fd_matches_digest(fd, digest):
        return fd
    os.close(fd)
    short = digest.split(":")[-1][:12]
    if evict:
        warn(f"Cached layer {short} does not match its digest; discarding "
             f"it so it can be fetched again.")
        try:
            os.unlink(layer_cache_path(digest))
        except OSError:
            pass
    else:
        warn(f"Cached layer {short} does not match its digest; ignoring it.")
    return None


def open_required_layer(digest: str, *, what: str = "Layer blob") -> int:
    """Open the cached blob for *digest*; raise if unusable. Descriptor.

    The no-refetch counterpart of open_verified_layer(): a locally built
    layer exists nowhere else, so a mismatch is reported and the file is
    left alone for the user to inspect rather than deleted out from
    under them. The caller closes the descriptor.
    """
    fd = _open_blob(digest)
    if fd is None:
        raise RuntimeError(
            f"{what} {digest} is missing from the layer cache."
        )
    if not fd_matches_digest(fd, digest):
        os.close(fd)
        raise RuntimeError(
            f"{what} {digest} does not match its digest; the layer cache "
            f"holds content that was not produced by this build. Rebuild "
            f"the image to repopulate the cache."
        )
    return fd


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
