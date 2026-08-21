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
#       swap the blob in between. The *directory* is a name too, and
#       O_NOFOLLOW on a composed LAYER_CACHE_DIR/<blob> covers only the
#       last component of it, so every blob is opened -- and an evicted
#       one unlinked -- as (dir_fd, name) off open_layer_cache_dir().
#
#   manifests/<sha256-prefix>.json
#       { "image_ref": ..., "arch": ...,
#         "manifest": ..., "repo": ..., "image_config": ... }
#       Key is the first 16 hex chars of sha256("<canonical_ref>_<arch>").
#
# Neither directory is reached by name. Both sit below BASE_CACHE_DIR,
# which on Termux is under the $TERMUX_PREFIX bound read-write into
# every non-isolated container, so `oci_manifests` (or `cache` one level
# above it) is a name a guest can leave behind as a symlink -- and
# os.listdir()/open() follow one. The manifest inventory that
# `list --image` prints, that `remove --image` deletes from, and that
# `clear-cache --orphan` computes its keep set from was doing exactly
# that: with `oci_manifests -> <host dir>` planted, resolving an image
# read the JSON out of that directory and the removal unlinked the file
# it found there. Every entry is now opened as (dir_fd, name) off a
# statedir.open_state_dir() walk, and a component that is not a plain
# directory is an error rather than something to follow.
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

from proot_distro import dirfd, statedir
from proot_distro.atomic import atomic_write
from proot_distro.constants import LAYER_CACHE_DIR, MANIFEST_CACHE_DIR
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


def manifest_layers(manifest, image_ref: str) -> list:
    """Return *manifest*'s layer descriptors, or raise RuntimeError.

    An image manifest reaches this program from two places and neither
    is its own: a registry sends one, and the manifest cache holds one
    -- a cache that is guest-writable on Termux, where RUNTIME_DIR sits
    under the bound $TERMUX_PREFIX. Every consumer then subscripts each
    descriptor for its digest and asks it for a mediaType or a size, so
    a manifest whose `layers` is a string, or whose entries are numbers,
    used to end `install` or `push` in a TypeError, an AttributeError or
    a KeyError -- none of which their handlers catch, so a traceback.

    A descriptor with no usable digest is fatal rather than skipped: an
    image's layers are an ordered stack, and quietly leaving one out
    produces a rootfs that is not the image that was asked for. The
    digest's *syntax* is still validate_digest's to judge, at the point
    it becomes a path or a request.
    """
    if not isinstance(manifest, dict):
        raise RuntimeError(
            f"Manifest for '{image_ref}' is malformed: not a JSON object."
        )
    layers = manifest.get("layers", [])
    if not isinstance(layers, list):
        raise RuntimeError(
            f"Manifest for '{image_ref}' is malformed: 'layers' is not "
            f"a list."
        )
    for layer in layers:
        if not isinstance(layer, dict) or not isinstance(
            layer.get("digest"), str
        ):
            raise RuntimeError(
                f"Manifest for '{image_ref}' is malformed: a layer "
                f"descriptor names no digest."
            )
    return layers


def layer_cache_name(digest: str) -> str:
    """Return the cached blob's file name for *digest*.

    The name on its own, for a caller addressing the blob as
    (dir_fd, name) off the layer cache's own descriptor.
    """
    validate_digest(digest)
    return digest.replace(":", "_")


def layer_cache_path(digest: str) -> str:
    """Return the on-disk path of the cached blob for *digest*.

    Refuses malformed digests so callers cannot accidentally route a
    crafted value past LAYER_CACHE_DIR via path traversal.

    For a *destination* (atomic_replace / publish_file both walk down to
    the directory themselves) and for messages. Nothing reads or unlinks
    through this: those go through _open_blob's descriptor, since the
    directory component is guest content the same way the blob is.
    """
    return os.path.join(LAYER_CACHE_DIR, layer_cache_name(digest))


def manifest_cache_key(image_ref: str, arch: str) -> str:
    """Return the manifest-cache key for (*image_ref*, *arch*)."""
    return hashlib.sha256(
        f"{canonical_ref(image_ref)}_{arch}".encode()
    ).hexdigest()[:16]


def manifest_cache_name(image_ref: str, arch: str) -> str:
    """Return the manifest-cache file name for (*image_ref*, *arch*)."""
    return manifest_cache_key(image_ref, arch) + ".json"


def manifest_cache_path(image_ref: str, arch: str) -> str:
    """Return the manifest-cache path for (*image_ref*, *arch*)."""
    return os.path.join(
        MANIFEST_CACHE_DIR, manifest_cache_name(image_ref, arch)
    )


def open_manifest_cache_dir() -> int:
    """Open MANIFEST_CACHE_DIR as a descriptor. Raises OSError.

    The walk statedir performs, one component at a time from the trust
    root with O_NOFOLLOW: the entries below it are this program's own
    files, but the directory holding them is guest-writable on Termux
    and its name is entirely predictable. FileNotFoundError means
    nothing has been cached yet; ENOTDIR means a component must not be
    followed, which is a reason to stop rather than to carry on as if
    the cache were empty. The caller owns the descriptor.
    """
    return statedir.open_state_dir(MANIFEST_CACHE_DIR)


def open_layer_cache_dir() -> int:
    """Open LAYER_CACHE_DIR as a descriptor. Raises OSError.

    open_manifest_cache_dir()'s counterpart for the blobs, and the same
    walk `clear-cache` reaches them by.
    """
    return statedir.open_state_dir(LAYER_CACHE_DIR)


def _load_entry(dir_fd: int, name: str):
    """Return the JSON payload of manifest-cache entry *name*, or None.

    Opened through open_regular_at, so a symlink or a FIFO left under an
    entry's name is not a cache entry: nothing but this program writes
    here, and one of those was planted. A payload that is not a JSON
    object is no entry either, and neither is one too large to be a
    manifest: the read is capped (statedir.read_state_file), since
    json.load() on the descriptor read to the end of a file the cache
    directory's writer chose the length of -- guest-writable on Termux,
    and `list --image` reads every entry in it.
    """
    try:
        fd, st = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    except OSError:
        return None
    try:
        payload = json.loads(statedir.read_state_file(fd))
    except (OSError, ValueError):
        # ValueError covers both a malformed document and a file that is
        # not text at all (UnicodeDecodeError), which used to escape as
        # a traceback out of `list --image`.
        return None
    finally:
        os.close(fd)
    if not isinstance(payload, dict):
        return None
    return payload, st


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
    with atomic_write(path, "w") as fh:
        json.dump(payload, fh)
    return path


def _read_entry(image_ref: str, arch: str):
    """Return the payload of the entry for (*image_ref*, *arch*), or None."""
    try:
        dir_fd = open_manifest_cache_dir()
    except OSError:
        return None
    try:
        loaded = _load_entry(dir_fd, manifest_cache_name(image_ref, arch))
    finally:
        os.close(dir_fd)
    return loaded[0] if loaded is not None else None


def annotate_manifest_cache(image_ref: str, arch: str) -> None:
    """Backfill ref/arch metadata onto an entry written by an older version.

    Called on a manifest-cache hit, where both values are known for
    certain. Entries that already carry them (and entries that cannot be
    read or rewritten) are left untouched, so this costs one small JSON
    read per cached pull and never invalidates anything.
    """
    payload = _read_entry(image_ref, arch)
    if payload is None or "manifest" not in payload:
        return
    if payload.get("image_ref") and payload.get("arch"):
        return
    payload["image_ref"] = image_ref
    payload["arch"] = arch
    try:
        # atomic_replace reaches a destination inside the state tree by
        # the same walk, so the rewrite lands in the directory the read
        # came out of.
        with atomic_write(manifest_cache_path(image_ref, arch), "w") as fh:
            json.dump(payload, fh)
    except OSError:
        pass


def load_manifest_cache(image_ref: str, arch: str):
    """Return (manifest, repo, image_config) from cache.

    On a cache miss (or read/parse error) returns ``(None, None, {})`` —
    callers check ``manifest is None`` to detect the miss.
    """
    payload = _read_entry(image_ref, arch)
    if payload is None:
        return None, None, {}
    try:
        return payload["manifest"], payload["repo"], \
            payload.get("image_config", {})
    except KeyError:
        return None, None, {}


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
    """Open the cache file for *digest* read-only. (dir_fd, fd), or None.

    Both descriptors, because the blob's *directory* is as much a name
    as the blob is. O_NOFOLLOW on a composed LAYER_CACHE_DIR/<name>
    covers the last component and nothing above it, so a guest that
    left `oci_layers -> <host dir>` behind had the entry read out of
    that directory -- and, when the digest did not match, unlinked from
    it. The walk statedir performs one component at a time from the
    trust root is what settles which directory this is; the entry is
    then named as (dir_fd, name) off the descriptor it validated.

    open_regular_at() is the rest of it: this directory is not only
    ours to write (see the module header), so the entry standing at a
    blob's name may be a symlink or a pipe someone else put there.

    The caller closes both descriptors -- the directory one last, since
    an eviction names the blob through it.
    """
    name = layer_cache_name(digest)
    try:
        dir_fd = open_layer_cache_dir()
    except OSError:
        return None
    try:
        fd, _st = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    except OSError:
        os.close(dir_fd)
        return None
    return dir_fd, fd


def blob_present(digest: str) -> bool:
    """True when the layer cache holds a usable blob for *digest*.

    The existence probe that goes with _open_blob(): reached by the
    same walk, so a planted parent cannot answer for the cache, and a
    symlink or a pipe under the blob's own name is not a blob. Says
    nothing about the content -- open_required_layer() is what checks
    that.
    """
    opened = _open_blob(digest)
    if opened is None:
        return False
    dir_fd, fd = opened
    os.close(fd)
    os.close(dir_fd)
    return True


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
    opened = _open_blob(digest)
    if opened is None:
        return None
    dir_fd, fd = opened
    try:
        if fd_matches_digest(fd, digest):
            return fd
        os.close(fd)
        short = digest.split(":")[-1][:12]
        if evict:
            warn(f"Cached layer {short} does not match its digest; "
                 f"discarding it so it can be fetched again.")
            # Named through the descriptor the walk validated. os.unlink()
            # on the composed path resolved `oci_layers` again, so a guest
            # that left it behind as a symlink had the host file standing
            # at the blob's name deleted instead.
            dirfd.unlink_quietly(dir_fd, layer_cache_name(digest))
        else:
            warn(f"Cached layer {short} does not match its digest; "
                 f"ignoring it.")
        return None
    finally:
        os.close(dir_fd)


def open_required_layer(digest: str, *, what: str = "Layer blob") -> int:
    """Open the cached blob for *digest*; raise if unusable. Descriptor.

    The no-refetch counterpart of open_verified_layer(): a locally built
    layer exists nowhere else, so a mismatch is reported and the file is
    left alone for the user to inspect rather than deleted out from
    under them. The caller closes the descriptor.
    """
    opened = _open_blob(digest)
    if opened is None:
        raise RuntimeError(
            f"{what} {digest} is missing from the layer cache."
        )
    dir_fd, fd = opened
    try:
        if not fd_matches_digest(fd, digest):
            os.close(fd)
            raise RuntimeError(
                f"{what} {digest} does not match its digest; the layer "
                f"cache holds content that was not produced by this "
                f"build. Rebuild the image to repopulate the cache."
            )
    finally:
        os.close(dir_fd)
    return fd


# ---------------------------------------------------------------------------
# Image inventory — what `list --image` and `remove --image` operate on
# ---------------------------------------------------------------------------

def _blob_size(layer_fd, digest: str):
    """Return the on-disk size of a layer blob, or None when unavailable.

    Named off the layer cache's own descriptor and lstat'ed, so a
    symlink planted under a blob's name measures the link rather than
    whatever host file it points at — and, not being a regular file,
    counts as no blob at all. A missing cache directory, a missing blob
    or a digest too malformed to map to a name are all the same answer:
    this layer occupies no cache space.
    """
    if layer_fd is None:
        return None
    try:
        st = dirfd.lstat_at(layer_fd, layer_cache_name(digest))
    except (OSError, RuntimeError):
        return None
    return st.st_size if stat.S_ISREG(st.st_mode) else None


def _read_record(dir_fd: int, name: str, layer_fd=None,
                 image_ref: str = "", arch: str = ""):
    """Build the cached-image record for manifest-cache entry *name*.

    *image_ref* / *arch* are fallbacks used when the entry predates
    those fields (see the module header). Returns None when the entry
    isn't a readable manifest-cache entry.
    """
    loaded = _load_entry(dir_fd, name)
    if loaded is None:
        return None
    payload, st = loaded
    manifest = payload.get("manifest")
    if not isinstance(manifest, dict):
        return None
    path = os.path.join(MANIFEST_CACHE_DIR, name)

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
        blob_size = _blob_size(layer_fd, layer["digest"])
        if blob_size is None:
            missing += 1
        else:
            size += blob_size

    return {
        "path": path,
        "name": name,
        "key": os.path.splitext(name)[0],
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
        "cached_at": st.st_mtime,
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
        dir_fd = open_manifest_cache_dir()
    except FileNotFoundError:
        return digests, unreadable
    except OSError:
        # The directory itself cannot be read, or is not a directory the
        # walk will follow: every entry in it is unaccounted for, so
        # report the directory as the blocker.
        return digests, [MANIFEST_CACHE_DIR]

    try:
        names = dirfd.listdir_at(dir_fd)
    except OSError:
        os.close(dir_fd)
        return digests, [MANIFEST_CACHE_DIR]

    try:
        for fname in names:
            # Entries are '<key>.json'; atomic_replace's in-flight
            # temporary files carry a '.tmp' suffix and are deliberately
            # not read.
            if not fname.endswith(".json"):
                continue
            path = os.path.join(MANIFEST_CACHE_DIR, fname)
            loaded = _load_entry(dir_fd, fname)
            if loaded is None:
                unreadable.append(path)
                continue
            manifest = loaded[0].get("manifest")
            if not isinstance(manifest, dict):
                unreadable.append(path)
                continue
            descriptors = list(manifest.get("layers") or [])
            descriptors.append(manifest.get("config"))
            for descriptor in descriptors:
                if isinstance(descriptor, dict) and descriptor.get("digest"):
                    digests.add(descriptor["digest"])
    finally:
        os.close(dir_fd)
    return digests, unreadable


def _ref_hints() -> dict:
    """Map cache key → (image_ref, arch) recovered from installed containers.

    Only used to name entries that predate the stored metadata: a
    container's manifest.json records the reference and architecture it
    was installed from, and the cache key is a pure function of those
    two, so any still-installed container identifies its own image.
    """
    from proot_distro.paths import (
        installed_container_names, read_container_manifest,
    )

    hints = {}
    for name in installed_container_names():
        try:
            data = read_container_manifest(name)
        except (OSError, ValueError):
            continue
        ref, arch = data.get("image_ref"), data.get("arch")
        if not ref or not arch:
            continue
        hints.setdefault(manifest_cache_key(ref, arch), (ref, arch))
    return hints


def image_cache_entry(image_ref: str, arch: str):
    """Return the cached-image record for (*image_ref*, *arch*), or None.

    Resolution goes through the cache key, so it finds legacy entries
    that carry no reference of their own just as reliably.
    """
    try:
        dir_fd = open_manifest_cache_dir()
    except OSError:
        return None
    layer_fd = _open_layers_quietly()
    try:
        return _read_record(
            dir_fd, manifest_cache_name(image_ref, arch), layer_fd,
            image_ref=canonical_ref(image_ref), arch=arch,
        )
    finally:
        os.close(dir_fd)
        if layer_fd is not None:
            os.close(layer_fd)


def _open_layers_quietly():
    """The layer cache's descriptor, or None when it cannot be walked.

    Only the blob *sizes* depend on it, and a cache that is not there
    yet is ordinary, so this is the one place the walk answers None
    instead of raising: an image with no blobs on disk is reported with
    every layer missing, which is what the user sees anyway.
    """
    try:
        return open_layer_cache_dir()
    except OSError:
        return None


def iter_cached_images() -> list:
    """Return one record per cached image, sorted by reference then arch.

    Unreadable entries are skipped. Records whose reference could not be
    determined sort last and carry an empty 'image_ref'.
    """
    try:
        dir_fd = open_manifest_cache_dir()
    except OSError:
        return []
    layer_fd = _open_layers_quietly()
    hints = None
    records = []
    try:
        names = sorted(dirfd.listdir_at(dir_fd))
    except OSError:
        names = []
    try:
        for fname in names:
            if not fname.endswith(".json"):
                continue
            record = _read_record(dir_fd, fname, layer_fd)
            if record is None:
                continue
            if not record["image_ref"]:
                if hints is None:
                    hints = _ref_hints()
                ref, arch = hints.get(record["key"], ("", ""))
                record["image_ref"] = ref
                record["arch"] = record["arch"] or arch
            records.append(record)
    finally:
        os.close(dir_fd)
        if layer_fd is not None:
            os.close(layer_fd)

    records.sort(
        key=lambda r: (not r["image_ref"], r["image_ref"], r["arch"], r["key"])
    )
    return records
