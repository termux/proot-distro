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

# Architecture: Final-stage output writers for `build`. Two output
# variants are supported:
#
#   - Variant A (`store_in_cache`): writes a manifest JSON to
#     MANIFEST_CACHE_DIR/<key>.json in the same shape that
#     helpers/docker._load_manifest_cache reads. Combined with the
#     layer blobs already deposited in LAYER_CACHE_DIR by the build
#     engine, this lets a subsequent `install <tag>` run entirely
#     offline (the existing pull_image() fully-offline branch fires).
#
#   - Variant B (`write_oci_archive`): assembles a standard OCI
#     image-layout tarball — oci-layout, index.json, blobs/sha256/*.
#     This is the same format `_extract_oci()` in commands/install.py
#     already consumes, so the round-trip install → backup → restore
#     remains uniform. The archive is also `docker load`-compatible.

import hashlib
import json
import os
import tarfile

from proot_distro.constants import (
    LAYER_CACHE_DIR,
    MANIFEST_CACHE_DIR,
)
from proot_distro.helpers.docker import (
    _layer_cache_path,
    _manifest_cache_path,
    parse_image_ref,
)


_OCI_MANIFEST_MEDIA = "application/vnd.oci.image.manifest.v1+json"
_OCI_CONFIG_MEDIA = "application/vnd.oci.image.config.v1+json"
_OCI_LAYER_MEDIA = "application/vnd.oci.image.layer.v1.tar+gzip"
_OCI_INDEX_MEDIA = "application/vnd.oci.image.index.v1+json"


def build_manifest_and_config(image_config, layers, arch_name):
    """Assemble the OCI image manifest and image config blobs.

    `image_config` is the in-progress config dict managed by the
    build engine. `layers` is the ordered list of
    {"digest", "size", "diff_id"} entries for this image. `arch_name`
    is the Docker arch name (e.g. "arm64", "amd64", "386").

    Returns (manifest_dict, image_config_dict). The image_config has
    `architecture`, `os`, and `rootfs.diff_ids` populated.
    """
    config = dict(image_config)
    config["architecture"] = arch_name
    config["os"] = config.get("os", "linux")
    config["rootfs"] = {
        "type": "layers",
        "diff_ids": [l["diff_id"] for l in layers],
    }
    config.setdefault("history", _default_history(layers))

    config_bytes = _canonical_json(config)
    config_digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()

    manifest = {
        "schemaVersion": 2,
        "mediaType": _OCI_MANIFEST_MEDIA,
        "config": {
            "mediaType": _OCI_CONFIG_MEDIA,
            "digest": config_digest,
            "size": len(config_bytes),
        },
        "layers": [
            {
                "mediaType": _OCI_LAYER_MEDIA,
                "digest": l["digest"],
                "size": l["size"],
            }
            for l in layers
        ],
    }
    return manifest, config


def _default_history(layers):
    return [
        {"created": "1970-01-01T00:00:00Z",
         "created_by": f"proot-distro build (layer {i + 1})"}
        for i, _ in enumerate(layers)
    ]


def _canonical_json(obj):
    """Return canonical (sorted-keys, no-whitespace) JSON bytes."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()


# ---------------------------------------------------------------------------
# Variant A — store the manifest in dlcache so install can find it
# ---------------------------------------------------------------------------

def store_in_cache(image_ref, arch_name_pd, manifest, image_config):
    """Write the manifest into MANIFEST_CACHE_DIR for offline install.

    `arch_name_pd` is the proot-distro arch (aarch64, x86_64, …). The
    cache key matches what helpers/docker._manifest_cache_path uses
    so that a subsequent `install <image_ref>` reads it on the
    fully-offline branch of pull_image().
    """
    _, repo, _ = parse_image_ref(image_ref)
    path = _manifest_cache_path(image_ref, arch_name_pd)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    payload = {
        "manifest": manifest,
        "repo": repo,
        "image_config": image_config,
    }
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return path


# ---------------------------------------------------------------------------
# Variant B — OCI image layout tarball
# ---------------------------------------------------------------------------

_TAR_MODES = {
    ".tar":     "w",
    ".oci.tar": "w",
    ".tar.gz":  "w:gz",
    ".tgz":     "w:gz",
    ".oci.tar.gz": "w:gz",
    ".tar.bz2": "w:bz2",
    ".tbz2":    "w:bz2",
    ".tar.xz":  "w:xz",
    ".txz":     "w:xz",
    ".oci.tar.xz": "w:xz",
}


def _detect_tar_mode(path):
    low = path.lower()
    # Order matters: try longer suffixes first.
    candidates = sorted(_TAR_MODES.keys(), key=len, reverse=True)
    for ext in candidates:
        if low.endswith(ext):
            return _TAR_MODES[ext]
    return "w"


def write_oci_archive(out_path, manifest, image_config, image_ref):
    """Write an OCI image-layout tarball.

    The layer blobs are expected to live in LAYER_CACHE_DIR under their
    standard digest-named filenames (the build engine writes them
    there). The function copies the blob bytes into the archive.

    A Docker-legacy `manifest.json` is also written at the archive
    root so the tarball is consumable by `docker load`, which falls
    back to a buggy 'per-directory legacy import' loop when only the
    OCI layout is present.
    """
    mode = _detect_tar_mode(out_path)
    config_bytes = _canonical_json(image_config)
    manifest_bytes = _canonical_json(manifest)
    config_digest_hex = hashlib.sha256(config_bytes).hexdigest()
    manifest_digest_hex = hashlib.sha256(manifest_bytes).hexdigest()

    # Manifest's config.digest must match what we just hashed.
    if manifest["config"]["digest"] != "sha256:" + config_digest_hex:
        manifest = dict(manifest)
        manifest["config"] = dict(manifest["config"])
        manifest["config"]["digest"] = "sha256:" + config_digest_hex
        manifest["config"]["size"] = len(config_bytes)
        manifest_bytes = _canonical_json(manifest)
        manifest_digest_hex = hashlib.sha256(manifest_bytes).hexdigest()

    index = {
        "schemaVersion": 2,
        "mediaType": _OCI_INDEX_MEDIA,
        "manifests": [
            {
                "mediaType": _OCI_MANIFEST_MEDIA,
                "digest": "sha256:" + manifest_digest_hex,
                "size": len(manifest_bytes),
                "annotations": {
                    "org.opencontainers.image.ref.name": image_ref,
                },
            }
        ],
    }
    index_bytes = _canonical_json(index)
    oci_layout_bytes = _canonical_json({"imageLayoutVersion": "1.0.0"})
    docker_manifest_bytes = _canonical_json(
        _build_docker_manifest(manifest, config_digest_hex, image_ref)
    )

    out_dir = os.path.dirname(os.path.abspath(out_path)) or "."
    os.makedirs(out_dir, exist_ok=True)

    tmp = out_path + ".tmp"
    try:
        with tarfile.open(tmp, mode) as tf:
            # oci-layout first so our own install probe detects the
            # OCI format on the first member it sees.
            _add_bytes(tf, "oci-layout", oci_layout_bytes)
            _add_bytes(tf, "index.json", index_bytes)
            _add_bytes(tf, "manifest.json", docker_manifest_bytes)
            _add_bytes(
                tf, f"blobs/sha256/{manifest_digest_hex}", manifest_bytes,
            )
            _add_bytes(
                tf, f"blobs/sha256/{config_digest_hex}", config_bytes,
            )
            for layer in manifest["layers"]:
                hex_digest = layer["digest"].split(":", 1)[1]
                src = _layer_cache_path(layer["digest"])
                if not os.path.isfile(src):
                    raise RuntimeError(
                        f"Layer blob {layer['digest']} is missing from the "
                        f"cache; cannot package OCI archive."
                    )
                _add_file(tf, src, f"blobs/sha256/{hex_digest}")
        os.replace(tmp, out_path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def _build_docker_manifest(manifest, config_digest_hex, image_ref):
    """Build the Docker-legacy `manifest.json` content.

    Used by `docker load` to find the image's config blob and ordered
    layer list inside the archive. Paths are tarball-relative.
    """
    layer_paths = []
    layer_sources = {}
    for layer in manifest.get("layers", []):
        digest = layer["digest"]
        hex_digest = digest.split(":", 1)[1]
        layer_paths.append(f"blobs/sha256/{hex_digest}")
        layer_sources[digest] = {
            "mediaType": layer.get("mediaType", _OCI_LAYER_MEDIA),
            "size": layer["size"],
            "digest": digest,
        }
    entry = {
        "Config": f"blobs/sha256/{config_digest_hex}",
        "RepoTags": [image_ref] if image_ref else [],
        "Layers": layer_paths,
    }
    if layer_sources:
        entry["LayerSources"] = layer_sources
    return [entry]


def _add_bytes(tf, arcname, data):
    import io
    tinfo = tarfile.TarInfo(arcname)
    tinfo.size = len(data)
    tinfo.mode = 0o644
    tinfo.mtime = 0
    tinfo.uid = 0
    tinfo.gid = 0
    tinfo.uname = ""
    tinfo.gname = ""
    tf.addfile(tinfo, io.BytesIO(data))


def _add_file(tf, src_path, arcname):
    st = os.stat(src_path)
    tinfo = tarfile.TarInfo(arcname)
    tinfo.size = st.st_size
    tinfo.mode = 0o644
    tinfo.mtime = 0
    tinfo.uid = 0
    tinfo.gid = 0
    tinfo.uname = ""
    tinfo.gname = ""
    with open(src_path, "rb") as fh:
        tf.addfile(tinfo, fh)
