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

# Architecture: Local-archive installs. Two formats are auto-detected
# by a streaming probe of the first 500 member names:
#
#   - OCI image layout (oci-layout marker present) — layer blobs are
#     unpacked into LAYER_CACHE_DIR and applied via apply_layer, mirroring
#     the on-disk shape produced by a Docker pull. Every blob read out of
#     the archive is hashed against the digest the archive names it by:
#     the file is a stranger's (`install ./img.tar`, or an http(s):// URL),
#     and LAYER_CACHE_DIR is shared with every image the user pulls, so an
#     unchecked blob would sit there under a digest a later pull trusts.
#   - Plain rootfs tar — extracted directly into the destination, with
#     a strip-count heuristic that figures out how many leading path
#     components to drop so well-known rootfs dirs (`etc`, `usr`, …)
#     land at the rootfs root.

import hashlib
import json
import os
import re
import sys
import tarfile

from proot_distro.atomic import atomic_replace
from proot_distro.compress import require_read_support
from proot_distro.message import log_info
from proot_distro.progress import clear_bar, progress_active
from proot_distro.helpers.docker import (
    ARCH_TO_DOCKER, DOCKER_TO_ARCH, apply_layer, layer_cache_path,
    open_verified_layer, require_data_digest, split_digest,
    validate_digest,
)
from proot_distro.progress import fmt_size
from proot_distro.helpers.tar_extract import extract_tar_to_rootfs


# Copy/hash slice for layer blobs coming out of an OCI archive.
_BLOB_CHUNK = 1024 * 1024

# How much of a JSON member is worth reading. index.json, an image
# manifest and an image config are all small by construction -- a few
# kilobytes, tens at the outside -- and every one of them is read whole
# into memory before it is parsed. The archive is a stranger's
# (`install ./img.tar`, or an http(s):// URL) and its member sizes are
# whatever it says they are, so an unbounded read is the archive's
# choice of how much memory this process allocates. 16 MiB is orders of
# magnitude above any real one and still bounded.
_MAX_JSON_BYTES = 16 * 1024 * 1024

# How many members are worth indexing. Blobs are addressed by digest in
# whatever order the manifest lists them, so the OCI branch needs random
# access and therefore a map -- which tf.getmembers() built over the
# whole archive, one TarInfo per member with no ceiling on how many the
# archive declares. A real OCI layout names a handful: `index.json`,
# `oci-layout`, `manifest.json`, and one blob per manifest, config and
# layer. Even a multi-arch index of twenty platforms stays in the low
# thousands.
_MAX_OCI_MEMBERS = 16384

# The names _oci_open_member() can ever be asked for: "index.json", and
# the blobs/<algo>/<hex> form _oci_blob_path() builds out of a digest
# validate_digest() has already accepted. Anything else in the archive
# is unaddressable here, so it is not worth a TarInfo -- which is what
# keeps a member list this reader cannot use from costing memory.
_OCI_INDEX_NAME = "index.json"
_OCI_BLOB_RE = re.compile(
    r"^blobs/[A-Za-z0-9]+(?:[+_.\-][A-Za-z0-9]+)*/[A-Fa-f0-9]+$"
)

# Top-level directory names that signal a rootfs filesystem root.
_ROOTFS_DIRS = frozenset({
    "bin", "dev", "etc", "home", "lib", "lib32", "lib64", "libx32",
    "media", "mnt", "opt", "proc", "root", "run", "sbin", "srv",
    "sys", "tmp", "usr", "var",
})


# ---------------------------------------------------------------------------
# Plain tar extraction
# ---------------------------------------------------------------------------

def detect_strip_count(member_names: list) -> int:
    """Return how many leading path components to strip so the first
    remaining component lands at the rootfs root (e.g. `etc`, `usr`).

    Tries strip counts 0–4, scores each by how many of the first 500
    names have a known rootfs dir at that depth, and picks the highest
    scorer.
    """
    sample = member_names[:500]
    best_strip, best_score = 0, -1
    for strip in range(5):
        score = 0
        for name in sample:
            parts = name.lstrip("/").rstrip("/").split("/")
            if len(parts) > strip and parts[strip] in _ROOTFS_DIRS:
                score += 1
        if score > best_score:
            best_score, best_strip = score, strip
    return best_strip


def extract_plain_tar(archive_path: str, strip: int, rootfs_fd: int) -> None:
    """Stream-extract a plain rootfs tarball into the *rootfs_fd* tree.

    Thin wrapper around extract_tar_to_rootfs that passes through the
    *strip* count and disables OCI whiteout handling (plain rootfs
    tarballs don't contain them). See the shared helper's docstring
    for the full set of invariants.
    """
    extract_tar_to_rootfs(archive_path, rootfs_fd, strip=strip)


# ---------------------------------------------------------------------------
# OCI image-layout extraction
# ---------------------------------------------------------------------------

def _oci_blob_path(digest: str) -> str:
    """Convert 'sha256:abc123' to 'blobs/sha256/abc123'.

    Validates the digest first so a crafted index.json cannot route
    the lookup through a member name with directory traversal (e.g.
    'blobs/../etc/passwd/...') even when the archive carries a
    matching forged member.
    """
    validate_digest(digest)
    algo, hex_val = digest.split(":", 1)
    return f"blobs/{algo}/{hex_val}"


def _oci_open_member(tf, member_map, path):
    """Return a readable file object for one regular member of the archive."""
    member = member_map.get(path)
    if member is None:
        raise RuntimeError(f"OCI archive is missing required file: {path}")
    if not member.isreg():
        # Reject hardlinks and symlinks: Python's extractfile() follows them
        # within the archive, letting a crafted outer tar redirect a read to
        # an unrelated member (e.g. a layer blob used as index.json).
        raise RuntimeError(f"OCI archive entry is not a regular file: {path}")
    fobj = tf.extractfile(member)
    if fobj is None:
        raise RuntimeError(f"OCI archive entry is not a regular file: {path}")
    return fobj


def _oci_read_capped(tf, member_map, path) -> bytes:
    """Read one JSON member whole, refusing one that is absurdly large.

    The size in the header is the archive's to declare and the member
    can lie about it either way, so the cap is applied to the bytes
    actually drawn rather than to member.size: one more byte than the
    limit is read, and its presence is the refusal.
    """
    fobj = _oci_open_member(tf, member_map, path)
    try:
        data = fobj.read(_MAX_JSON_BYTES + 1)
    finally:
        fobj.close()
    if len(data) > _MAX_JSON_BYTES:
        raise RuntimeError(
            f"OCI archive entry '{path}' is larger than "
            f"{_MAX_JSON_BYTES} bytes; refusing to read it."
        )
    return data


def _oci_json_object(data: bytes, what: str) -> dict:
    """Parse *data* as a JSON object, or raise RuntimeError saying so.

    The archive is a stranger's file and every document in it is read to
    be subscripted, so "not JSON" and "JSON, but a list" are both things
    it can say. Either used to escape install's handler as a ValueError
    or an AttributeError -- a traceback, since only EOFError, OSError,
    TarError and RuntimeError are caught there.
    """
    try:
        payload = json.loads(data)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"{what} is not valid JSON. The archive is corrupt or is not "
            f"an OCI image."
        ) from exc
    if not isinstance(payload, dict):
        raise RuntimeError(
            f"{what} is not a JSON object. The archive is corrupt or is "
            f"not an OCI image."
        )
    return payload


def _oci_digest(entry, what: str) -> str:
    """Return *entry*'s digest string, or raise RuntimeError.

    Every blob below index.json is addressed by one, and the value is
    the archive's to write: a descriptor that is not an object, or whose
    digest is absent or is not a string, names nothing this can read.
    """
    if not isinstance(entry, dict):
        raise RuntimeError(
            f"{what} is malformed: expected an object describing a blob."
        )
    digest = entry.get("digest")
    if not isinstance(digest, str) or not digest:
        raise RuntimeError(f"{what} names no digest.")
    return digest


def _oci_read_json(tf, member_map, path):
    """Extract a member from the outer archive and parse it as JSON."""
    return _oci_json_object(
        _oci_read_capped(tf, member_map, path),
        f"OCI archive entry '{path}'",
    )


def _oci_read_blob_json(tf, member_map, digest):
    """Read the JSON blob named by *digest* and check that it hashes to it.

    index.json is the archive's root of trust and is read by name; every
    step below it — the image manifest, the image config — is addressed
    by digest, so a swapped blob is caught here rather than believed.
    """
    path = _oci_blob_path(digest)
    data = _oci_read_capped(tf, member_map, path)
    require_data_digest(data, digest, what=f"OCI archive blob '{path}'")
    return _oci_json_object(data, f"OCI archive blob '{path}'")


def _oci_find_manifest_entry(tf, member_map, index_manifests, dist_arch):
    """Pick the index manifest entry matching *dist_arch*.

    Strategy:
      1. Single entry: trust the caller and use it regardless of arch.
      2. Multiple entries with platform.architecture: filter by arch.
      3. Multiple entries without platform: read each config blob.
    """
    if len(index_manifests) == 1:
        return index_manifests[0]

    docker_arch = ARCH_TO_DOCKER.get(dist_arch, (dist_arch, ""))[0]

    # A "platform" that is not an object describes nothing, so the entry
    # takes the slow path with the ones that carry no platform at all
    # rather than deciding the architecture on a value nothing can read.
    platform_entries = [
        e for e in index_manifests if isinstance(e.get("platform"), dict)
    ]
    if platform_entries:
        for entry in platform_entries:
            p = entry["platform"]
            if p.get("architecture") == docker_arch and p.get("os") == "linux":
                return entry
        raise RuntimeError(
            f"No manifest found for architecture '{dist_arch}' "
            f"in OCI index (tried {docker_arch})."
        )

    # Slow path: read each manifest → config to detect architecture.
    for entry in index_manifests:
        manifest = _oci_read_blob_json(
            tf, member_map, _oci_digest(entry, "OCI index manifest entry"),
        )
        config = manifest.get("config", {})
        config_digest = (
            config.get("digest", "") if isinstance(config, dict) else ""
        )
        if not isinstance(config_digest, str) or not config_digest:
            continue
        image_config = _oci_read_blob_json(tf, member_map, config_digest)
        if image_config.get("architecture") == docker_arch:
            return entry

    raise RuntimeError(
        f"No manifest found for architecture '{dist_arch}' "
        f"in OCI image (tried {docker_arch})."
    )


def _oci_cache_layer(tf, member_map, digest):
    """Extract a layer blob from the outer archive into LAYER_CACHE_DIR.

    Returns an open descriptor on the cached blob; the caller closes it.
    The bytes are hashed as they are copied and the blob is promoted
    only if it matches *digest*. The cache is shared with every image
    the user pulls and a cached blob is reused on the strength of its
    name, so a layer that does not hash to the digest the archive names
    it by must never reach it — otherwise one crafted archive silently
    replaces a layer of any image whose digests it chooses to claim.

    The descriptor is taken on the temporary before it is renamed into
    place, so it is bound to the inode these bytes went into rather than
    to a name that something else may claim afterwards.
    """
    blob_path = _oci_blob_path(digest)
    _algo, expected_hex = split_digest(digest)
    fobj = _oci_open_member(tf, member_map, blob_path)
    cache_path = layer_cache_path(digest)
    hasher = hashlib.sha256()
    try:
        # atomic_replace removes the temporary on any exception, so a
        # mismatch leaves nothing behind for a later pull to pick up.
        with atomic_replace(cache_path) as tmp_fd:
            with open(tmp_fd, "wb", closefd=False) as out:
                while True:
                    chunk = fobj.read(_BLOB_CHUNK)
                    if not chunk:
                        break
                    hasher.update(chunk)
                    out.write(chunk)
            actual_hex = hasher.hexdigest()
            if actual_hex != expected_hex:
                raise RuntimeError(
                    f"OCI archive layer blob '{blob_path}' does not match "
                    f"its digest (expected {digest}, got "
                    f"sha256:{actual_hex}). The archive is corrupt or was "
                    f"tampered with."
                )
            # A second descriptor on the same inode: atomic_replace
            # closes the one it yielded once the rename is done, and
            # the caller reads these bytes rather than the name they
            # were published under.
            fd = os.dup(tmp_fd)
            os.lseek(fd, 0, os.SEEK_SET)
    finally:
        fobj.close()
    return fd


def _extract_oci(tf, member_map, rootfs_fd, dist_arch):
    """Install from an OCI image layout (tf already open).

    Reads index.json, selects the manifest for *dist_arch*, caches each
    layer blob in LAYER_CACHE_DIR, and applies the layers via apply_layer.

    Returns a metadata dict compatible with the manifest.json schema:
        manifest, image_config, image_ref, arch.
    """
    index = _oci_read_json(tf, member_map, "index.json")
    index_manifests = index.get("manifests", [])
    if not isinstance(index_manifests, list):
        raise RuntimeError(
            "OCI index.json is malformed: 'manifests' is not a list."
        )
    index_manifests = [e for e in index_manifests if isinstance(e, dict)]
    if not index_manifests:
        raise RuntimeError("OCI index.json contains no manifests.")

    manifest_entry = _oci_find_manifest_entry(
        tf, member_map, index_manifests, dist_arch
    )

    manifest = _oci_read_blob_json(
        tf, member_map,
        _oci_digest(manifest_entry, "OCI index manifest entry"),
    )

    config = manifest.get("config", {})
    if not isinstance(config, dict):
        raise RuntimeError("OCI image manifest has a malformed config.")
    config_digest = config.get("digest", "")
    if not isinstance(config_digest, str) or not config_digest:
        raise RuntimeError("OCI image manifest has no config digest.")
    image_config = _oci_read_blob_json(tf, member_map, config_digest)

    docker_arch = image_config.get("architecture", "")
    actual_arch = DOCKER_TO_ARCH.get(
        docker_arch if isinstance(docker_arch, str) else "", dist_arch,
    )

    layers = manifest.get("layers", [])
    if not isinstance(layers, list):
        raise RuntimeError(
            "OCI image manifest is malformed: 'layers' is not a list."
        )
    if not layers:
        raise RuntimeError("OCI image manifest contains no layers.")
    # Every layer is applied in order, so one that names no readable
    # digest is fatal rather than skipped: the result would not be the
    # image the archive describes.
    for layer in layers:
        _oci_digest(layer, "OCI image layer")

    n_layers = len(layers)
    for i, layer in enumerate(layers):
        digest = layer["digest"]
        short_id = digest[:19]
        size = layer.get("size", 0)
        size_str = (f" ({fmt_size(size)})"
                    if isinstance(size, int) and size > 0 else "")
        # A descriptor, not a name: the blob is hashed and then read, and
        # naming it twice is what lets it change in between (see
        # cache.open_verified_layer).
        layer_fd = open_verified_layer(digest)

        if layer_fd is not None:
            log_info(f"{short_id}: Layer {i + 1}/{n_layers} already cached, "
                     f"skipping.")
        else:
            # Absent, or present with content that no longer hashes to its
            # digest — either way the archive in hand is the better copy.
            log_info(f"{short_id}: Caching layer "
                     f"{i + 1}/{n_layers}{size_str}...")
            layer_fd = _oci_cache_layer(tf, member_map, digest)

        log_info(f"{short_id}: Applying layer {i + 1}/{n_layers}...")
        try:
            apply_layer(layer_fd, rootfs_fd, digest=digest)
        finally:
            try:
                os.close(layer_fd)
            except OSError:
                pass

    annotations = manifest_entry.get("annotations", {})
    if not isinstance(annotations, dict):
        annotations = {}
    image_ref = (
        annotations.get("io.containerd.image.name")
        or annotations.get("org.opencontainers.image.ref.name")
        or ""
    )
    if not isinstance(image_ref, str):
        image_ref = ""

    return {
        "manifest": manifest,
        "image_config": image_config,
        "image_ref": image_ref,
        "arch": actual_arch,
    }


# ---------------------------------------------------------------------------
# Format-detecting entry point
# ---------------------------------------------------------------------------

def _index_oci_members(tf) -> dict:
    """Map the addressable members of an OCI layout archive by name.

    tf.getmembers() was the whole of this, and it holds a TarInfo for
    every member the archive declares — however many that is, and
    however long their names. The two things that bound it are here
    instead: the scan stops at _MAX_OCI_MEMBERS (a hard refusal, not a
    silent truncation, since a half-indexed archive would surface as a
    missing blob), and only names _oci_open_member() can be asked for
    are kept.

    Later members win, which is what building the dict over
    getmembers() did, and what tar itself means by a repeated name.
    """
    member_map: dict = {}
    scanned = 0
    for member in tf:
        scanned += 1
        if scanned > _MAX_OCI_MEMBERS:
            raise RuntimeError(
                f"OCI archive declares more than {_MAX_OCI_MEMBERS} "
                f"entries; refusing to index it."
            )
        name = member.name
        if name == _OCI_INDEX_NAME or _OCI_BLOB_RE.match(name):
            member_map[name] = member
    return member_map


def install_from_local_file(
    archive_path: str, rootfs_fd: int, dist_arch: str
):
    """Open *archive_path*, detect its format, and extract into *rootfs_fd*.

    Returns a metadata dict for OCI images with keys
    ``{manifest, image_config, image_ref, arch}`` — a superset of what
    pull_image returns, since a local OCI archive can also surface the
    embedded image reference and architecture. Returns ``None`` for
    plain tarballs (no manifest.json is written for those).

    Detection uses a streaming probe that reads at most the first 500
    member headers — fast even on compressed multi-GB images.
    """
    # A zstd outer archive needs Python 3.14; without it the probe below
    # sees a corrupt tar and would report it as one.
    require_read_support(archive_path, f"archive '{archive_path}'")

    # Streaming probe: read up to 500 member names to detect OCI
    # layout and determine the strip count for plain tarballs. For
    # compressed archives this decompresses only the leading portion.
    probe_names: list = []
    is_oci = False
    with tarfile.open(archive_path, "r|*") as tf_probe:
        for m in tf_probe:
            probe_names.append(m.name)
            if m.name == "oci-layout":
                is_oci = True
                break
            if len(probe_names) >= 500:
                break

    if is_oci:
        # OCI image layout: blobs are accessed by digest in arbitrary
        # order, so random access — and therefore an index — is required.
        with tarfile.open(archive_path, "r:*") as tf:
            if progress_active():
                log_info("Indexing OCI archive...")
                sys.stderr.flush()
            try:
                member_map = _index_oci_members(tf)
            finally:
                clear_bar()
            return _extract_oci(tf, member_map, rootfs_fd, dist_arch)

    strip = detect_strip_count(probe_names)
    extract_plain_tar(archive_path, strip, rootfs_fd)
    return None
