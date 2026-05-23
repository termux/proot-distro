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
#     helpers/docker.load_manifest_cache reads. Combined with the
#     layer blobs already deposited in LAYER_CACHE_DIR by the build
#     engine, this lets a subsequent `install <tag>` run entirely
#     offline (the existing pull_image() fully-offline branch fires).
#
#   - Variant B (`write_oci_archive`): assembles a standard OCI
#     image-layout tarball — oci-layout, index.json, blobs/sha256/*.
#     This is the same format `_extract_oci()` in
#     commands/install_local.py already consumes, so a tarball written
#     by `build -o` can be fed straight back into `install`. The
#     archive is also `docker load`-compatible.

import hashlib
import json
import os
import tarfile

from proot_distro.atomic import atomic_replace
from proot_distro.helpers.docker import (
    layer_cache_path,
    manifest_cache_path,
    parse_image_ref,
)
from proot_distro.helpers.docker.media import (
    OCI_CONFIG_MEDIA,
    OCI_INDEX_MEDIA,
    OCI_LAYER_MEDIA,
    OCI_MANIFEST_MEDIA,
    canonical_json,
)


def build_manifest_and_config(image_config, layers, arch_name):
    """Assemble the OCI image manifest and image config blobs.

    `image_config` is the in-progress config dict managed by the
    build engine — its `history` array is taken verbatim (the engine
    appends one entry per dispatched instruction so the count of
    non-empty-layer entries already matches len(layers)). `layers` is
    the ordered list of {"digest", "size", "diff_id"} entries for
    this image. `arch_name` is the Docker arch name (e.g. "arm64",
    "amd64", "386").

    Returns (manifest_dict, image_config_dict). The image_config has
    `architecture`, `os`, and `rootfs.diff_ids` populated and carries
    whatever `history` the engine produced.
    """
    config = dict(image_config)
    config["architecture"] = arch_name
    config["os"] = config.get("os", "linux")
    config["rootfs"] = {
        "type": "layers",
        "diff_ids": [l["diff_id"] for l in layers],
    }
    # Defensive: every code path that reaches here is expected to have
    # populated history during dispatch. The setdefault is just so
    # tests / future callers that construct an image_config by hand
    # don't blow up on a missing key.
    config.setdefault("history", [])

    config_bytes = canonical_json(config)
    config_digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()

    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA,
        "config": {
            "mediaType": OCI_CONFIG_MEDIA,
            "digest": config_digest,
            "size": len(config_bytes),
        },
        "layers": [
            {
                "mediaType": OCI_LAYER_MEDIA,
                "digest": l["digest"],
                "size": l["size"],
            }
            for l in layers
        ],
    }
    return manifest, config


# ---------------------------------------------------------------------------
# Variant A — store the manifest in dlcache so install can find it
# ---------------------------------------------------------------------------

def store_in_cache(image_ref, arch_name_pd, manifest, image_config):
    """Write the manifest into MANIFEST_CACHE_DIR for offline install.

    `arch_name_pd` is the proot-distro arch (aarch64, x86_64, …). The
    cache key matches what helpers/docker.manifest_cache_path uses
    so that a subsequent `install <image_ref>` reads it on the
    fully-offline branch of pull_image().
    """
    _, repo, _ = parse_image_ref(image_ref)
    path = manifest_cache_path(image_ref, arch_name_pd)
    payload = {
        "manifest": manifest,
        "repo": repo,
        "image_config": image_config,
    }
    with atomic_replace(path) as tmp:
        with open(tmp, "w") as fh:
            json.dump(payload, fh, indent=2, sort_keys=True)
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
    config_bytes = canonical_json(image_config)
    manifest_bytes = canonical_json(manifest)
    config_digest_hex = hashlib.sha256(config_bytes).hexdigest()
    manifest_digest_hex = hashlib.sha256(manifest_bytes).hexdigest()

    # Manifest's config.digest must match what we just hashed.
    if manifest["config"]["digest"] != "sha256:" + config_digest_hex:
        manifest = dict(manifest)
        manifest["config"] = dict(manifest["config"])
        manifest["config"]["digest"] = "sha256:" + config_digest_hex
        manifest["config"]["size"] = len(config_bytes)
        manifest_bytes = canonical_json(manifest)
        manifest_digest_hex = hashlib.sha256(manifest_bytes).hexdigest()

    index = {
        "schemaVersion": 2,
        "mediaType": OCI_INDEX_MEDIA,
        "manifests": [
            {
                "mediaType": OCI_MANIFEST_MEDIA,
                "digest": "sha256:" + manifest_digest_hex,
                "size": len(manifest_bytes),
                "annotations": {
                    "org.opencontainers.image.ref.name": image_ref,
                },
            }
        ],
    }
    index_bytes = canonical_json(index)
    oci_layout_bytes = canonical_json({"imageLayoutVersion": "1.0.0"})
    docker_manifest_bytes = canonical_json(
        _build_docker_manifest(manifest, config_digest_hex, image_ref)
    )

    with atomic_replace(os.path.abspath(out_path)) as tmp:
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
                src = layer_cache_path(layer["digest"])
                if not os.path.isfile(src):
                    raise RuntimeError(
                        f"Layer blob {layer['digest']} is missing from the "
                        f"cache; cannot package OCI archive."
                    )
                _add_file(tf, src, f"blobs/sha256/{hex_digest}")


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
            "mediaType": layer.get("mediaType", OCI_LAYER_MEDIA),
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
