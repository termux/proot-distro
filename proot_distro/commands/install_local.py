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
#     the on-disk shape produced by a Docker pull.
#   - Plain rootfs tar — extracted directly into the destination, with
#     a strip-count heuristic that figures out how many leading path
#     components to drop so well-known rootfs dirs (`etc`, `usr`, …)
#     land at the rootfs root.

import json
import os
import shutil
import sys
import tarfile

from proot_distro.atomic import atomic_replace
from proot_distro.message import log_info
from proot_distro.progress import clear_bar, progress_active
from proot_distro.helpers.docker import (
    ARCH_TO_DOCKER, apply_layer, layer_cache_path, validate_digest,
)
from proot_distro.progress import fmt_size
from proot_distro.helpers.tar_extract import extract_tar_to_rootfs


# Top-level directory names that signal a rootfs filesystem root.
_ROOTFS_DIRS = frozenset({
    "bin", "dev", "etc", "home", "lib", "lib32", "lib64", "libx32",
    "media", "mnt", "opt", "proc", "root", "run", "sbin", "srv",
    "sys", "tmp", "usr", "var",
})


# Reverse of ARCH_TO_DOCKER: Docker architecture name → proot-distro arch.
_DOCKER_TO_ARCH = {docker: pd for pd, (docker, _) in ARCH_TO_DOCKER.items()}


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


def extract_plain_tar(archive_path: str, strip: int, rootfs_dir: str) -> None:
    """Stream-extract a plain rootfs tarball into *rootfs_dir*.

    Thin wrapper around extract_tar_to_rootfs that passes through the
    *strip* count and disables OCI whiteout handling (plain rootfs
    tarballs don't contain them). See the shared helper's docstring
    for the full set of invariants.
    """
    extract_tar_to_rootfs(archive_path, rootfs_dir, strip=strip)


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


def _oci_read_json(tf, member_map, path):
    """Extract a member from the outer archive and parse it as JSON."""
    member = member_map.get(path)
    if member is None:
        raise RuntimeError(f"OCI archive is missing required file: {path}")
    fobj = tf.extractfile(member)
    if fobj is None:
        raise RuntimeError(f"OCI archive entry is not a regular file: {path}")
    try:
        return json.loads(fobj.read())
    finally:
        fobj.close()


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

    platform_entries = [e for e in index_manifests if "platform" in e]
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
        manifest = _oci_read_json(
            tf, member_map, _oci_blob_path(entry["digest"])
        )
        config_digest = manifest.get("config", {}).get("digest", "")
        if not config_digest:
            continue
        config = _oci_read_json(
            tf, member_map, _oci_blob_path(config_digest)
        )
        if config.get("architecture") == docker_arch:
            return entry

    raise RuntimeError(
        f"No manifest found for architecture '{dist_arch}' "
        f"in OCI image (tried {docker_arch})."
    )


def _oci_cache_layer(tf, member_map, digest):
    """Extract a layer blob from the outer archive into LAYER_CACHE_DIR."""
    blob_path = _oci_blob_path(digest)
    member = member_map.get(blob_path)
    if member is None:
        raise RuntimeError(f"OCI archive is missing layer blob: {blob_path}")
    fobj = tf.extractfile(member)
    if fobj is None:
        raise RuntimeError(
            f"OCI layer blob is not a regular file: {blob_path}"
        )
    cache_path = layer_cache_path(digest)
    try:
        with atomic_replace(cache_path) as tmp:
            with open(tmp, "wb") as out:
                shutil.copyfileobj(fobj, out)
    finally:
        fobj.close()
    return cache_path


def _extract_oci(tf, member_map, rootfs_dir, dist_arch):
    """Install from an OCI image layout (tf already open).

    Reads index.json, selects the manifest for *dist_arch*, caches each
    layer blob in LAYER_CACHE_DIR, and applies the layers via apply_layer.

    Returns a metadata dict compatible with the manifest.json schema:
        manifest, image_config, image_ref, arch.
    """
    index = _oci_read_json(tf, member_map, "index.json")
    index_manifests = index.get("manifests", [])
    if not index_manifests:
        raise RuntimeError("OCI index.json contains no manifests.")

    manifest_entry = _oci_find_manifest_entry(
        tf, member_map, index_manifests, dist_arch
    )

    manifest = _oci_read_json(
        tf, member_map, _oci_blob_path(manifest_entry["digest"])
    )

    config_digest = manifest.get("config", {}).get("digest", "")
    if not config_digest:
        raise RuntimeError("OCI image manifest has no config digest.")
    image_config = _oci_read_json(tf, member_map, _oci_blob_path(config_digest))

    docker_arch = image_config.get("architecture", "")
    actual_arch = _DOCKER_TO_ARCH.get(docker_arch, dist_arch)

    layers = manifest.get("layers", [])
    if not layers:
        raise RuntimeError("OCI image manifest contains no layers.")

    n_layers = len(layers)
    for i, layer in enumerate(layers):
        digest = layer["digest"]
        short_id = digest[:19]
        size = layer.get("size", 0)
        size_str = f" ({fmt_size(size)})" if size else ""
        cache_path = layer_cache_path(digest)

        if os.path.isfile(cache_path):
            log_info(f"{short_id}: Layer {i + 1}/{n_layers} already cached, "
                     f"skipping.")
        else:
            log_info(f"{short_id}: Caching layer "
                     f"{i + 1}/{n_layers}{size_str}...")
            _oci_cache_layer(tf, member_map, digest)

        log_info(f"{short_id}: Applying layer {i + 1}/{n_layers}...")
        apply_layer(cache_path, rootfs_dir)

    annotations = manifest_entry.get("annotations", {})
    image_ref = (
        annotations.get("io.containerd.image.name")
        or annotations.get("org.opencontainers.image.ref.name")
        or ""
    )

    return {
        "manifest": manifest,
        "image_config": image_config,
        "image_ref": image_ref,
        "arch": actual_arch,
    }


# ---------------------------------------------------------------------------
# Format-detecting entry point
# ---------------------------------------------------------------------------

def install_from_local_file(
    archive_path: str, rootfs_dir: str, dist_arch: str
):
    """Open *archive_path*, detect its format, and extract into *rootfs_dir*.

    Returns a metadata dict for OCI images with keys
    ``{manifest, image_config, image_ref, arch}`` — a superset of what
    pull_image returns, since a local OCI archive can also surface the
    embedded image reference and architecture. Returns ``None`` for
    plain tarballs (no manifest.json is written for those).

    Detection uses a streaming probe that reads at most the first 500
    member headers — fast even on compressed multi-GB images.
    """
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
        # order, so random access via getmembers() is required.
        with tarfile.open(archive_path, "r:*") as tf:
            if progress_active():
                log_info("Indexing OCI archive...")
                sys.stderr.flush()
            raw_members = tf.getmembers()
            clear_bar()
            member_map = {m.name: m for m in raw_members}
            return _extract_oci(tf, member_map, rootfs_dir, dist_arch)

    strip = detect_strip_count(probe_names)
    extract_plain_tar(archive_path, strip, rootfs_dir)
    return None
