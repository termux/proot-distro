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

# Architecture: The pull pipeline.
#
#   1. Check the local manifest cache. If present, decide whether all
#      layer blobs are already on disk (fully-offline branch) or only
#      a token + the missing layers need to be fetched.
#   2. On manifest miss, resolve the registry manifest. Manifest-list
#      indexes are unwrapped to the arch-specific child manifest.
#   3. For each layer: skip when a cached blob re-hashes to its digest,
#      otherwise download_blob. Apply the layer onto the supplied rootfs
#      directory.
#   4. Return a small metadata dict the caller can use to write
#      containers/<name>/manifest.json and surface image labels.
#
# Everything addressed by digest is checked against it before use: the
# arch-specific manifest fetched out of an index, the config blob, and
# every layer blob — whether it arrived over the network or was already
# sitting in the cache. Neither a cache file's name nor a registry's
# word is treated as evidence of content.

import json
import urllib.error
import urllib.request

from proot_distro.compress import ZSTD_AVAILABLE, unsupported_msg
from proot_distro.message import log_info, log_error
from proot_distro.progress import fmt_size
from proot_distro.helpers.download import retry_http
from proot_distro.helpers.docker.cache import (
    annotate_manifest_cache,
    load_manifest_cache,
    require_data_digest,
    save_manifest_cache,
    verified_layer_path,
)
from proot_distro.helpers.docker.layers import apply_layer, download_blob
from proot_distro.helpers.docker.media import (
    DOCKER_MANIFEST_LIST_MEDIA,
    DOCKER_MANIFEST_MEDIA,
    OCI_INDEX_MEDIA,
    OCI_MANIFEST_MEDIA,
)
from proot_distro.helpers.docker.refs import ARCH_TO_DOCKER, parse_image_ref
from proot_distro.helpers.docker.transport import (
    auth_denied_msg,
    auth_note,
    get_auth_token,
    opener,
    _ua,
)


# Manifest media types treated as an index (multi-arch list).
_MANIFEST_LIST_TYPES = frozenset({
    DOCKER_MANIFEST_LIST_MEDIA, OCI_INDEX_MEDIA,
})

# Accepted manifest media types, ordered by preference (index first).
_ACCEPT_HEADER = ", ".join([
    OCI_INDEX_MEDIA,
    DOCKER_MANIFEST_LIST_MEDIA,
    OCI_MANIFEST_MEDIA,
    DOCKER_MANIFEST_MEDIA,
])


def _get_manifest(
    repo: str, ref: str, token: str, base: str,
    insecure: bool = False,
) -> dict:
    url = f"{base}/v2/{repo}/manifests/{ref}"
    headers = {**_ua(), "Accept": _ACCEPT_HEADER}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)

    def _attempt():
        with opener(insecure).open(req) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")

    body, ct = retry_http(_attempt, what=f"Fetching manifest {ref}")
    # A reference containing ':' is a digest, not a tag: the registry's
    # answer is content-addressed, so it has to hash to what we asked
    # for. This is the only check standing between a hostile mirror (or
    # an --allow-insecure MITM) and an arbitrary layer list.
    if ":" in ref:
        require_data_digest(body, ref, what=f"Manifest for '{repo}'")
    data = json.loads(body)
    # Prefer the Content-Type header; fall back to the mediaType field.
    data["_ct"] = ct.split(";")[0].strip() or data.get("mediaType", "")
    return data


def _pick_platform(
    entries: list, arch: str, variant: str, image_ref: str
) -> dict:
    """Find the manifest list entry matching arch (and optionally variant)."""
    # Exact match first (arch + non-empty variant must match).
    for entry in entries:
        plat = entry.get("platform", {})
        if plat.get("os", "linux") != "linux":
            continue
        if plat.get("architecture") != arch:
            continue
        if variant and plat.get("variant", "") not in (variant, ""):
            continue
        return entry

    # Variant-agnostic fallback.
    for entry in entries:
        plat = entry.get("platform", {})
        if (plat.get("os", "linux") == "linux"
                and plat.get("architecture") == arch):
            return entry

    available = []
    for e in entries:
        plat = e.get("platform", {})
        if plat.get("os", "linux") != "linux":
            continue
        a = plat.get("architecture", "?")
        v = plat.get("variant", "")
        available.append(f"{a}/{v}" if v else a)
    raise RuntimeError(
        f"No image found for architecture '{arch}' in '{image_ref}'. "
        f"Available Linux platforms: {', '.join(available) or 'none'}. "
        f"Visit https://hub.docker.com to look for alternatives."
    )


def _resolve_single_manifest(
    image_ref: str, arch: str, insecure: bool = False
) -> tuple:
    """Return (single_image_manifest, token, repo, base) for the arch."""
    registry, repo, tag = parse_image_ref(image_ref)

    log_info(f"Authenticating with registry{auth_note()}...")
    token, base = get_auth_token(repo, registry, insecure=insecure)

    log_info(f"Fetching manifest for '{image_ref}'...")
    manifest = _get_manifest(repo, tag, token, base, insecure)

    if manifest["_ct"] in _MANIFEST_LIST_TYPES or "manifests" in manifest:
        docker_arch, docker_variant = ARCH_TO_DOCKER.get(arch, (arch, ""))
        target = _pick_platform(
            manifest.get("manifests", []),
            docker_arch,
            docker_variant,
            image_ref,
        )
        log_info(f"Fetching {arch} manifest...")
        manifest = _get_manifest(
            repo, target["digest"], token, base, insecure
        )

    return manifest, token, repo, base


def _fetch_config_blob(
    repo: str, cfg_digest: str, token: str, base: str,
    insecure: bool = False,
) -> dict:
    """Fetch the image config blob; return parsed dict (empty on error).

    A blob that does not hash to *cfg_digest* is fatal rather than
    empty: this config supplies Entrypoint/Cmd/Env, which `run` and
    `login` go on to execute, and it is persisted into the manifest
    cache. An unreachable or unparsable config is merely degraded and
    still answers {} the way it always has.
    """
    if not cfg_digest:
        return {}
    try:
        url = f"{base}/v2/{repo}/blobs/{cfg_digest}"
        headers = {**_ua()}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)

        def _attempt():
            with opener(insecure).open(req) as resp:
                return resp.read()

        body = retry_http(_attempt, what="Fetching image config")
    except Exception:
        return {}

    require_data_digest(body, cfg_digest, what="Image config blob")
    try:
        return json.loads(body)
    except (ValueError, TypeError):
        return {}


def _usable_cached_layers(layers: list) -> dict:
    """Return ``{digest: path}`` for every layer blob already usable.

    Usable means present *and* hashing to its own digest. A blob that
    fails is dropped by verified_layer_path(), so it is simply absent
    here and gets downloaded again like any other missing layer.

    Deciding this once per pull, before the first apply, is what keeps
    the cost at one hash per blob: the "is it all cached?" question and
    the per-layer "download or reuse?" question read the same answer.
    """
    usable = {}
    for layer in layers:
        digest = layer.get("digest")
        if not digest or digest in usable:
            continue
        path = verified_layer_path(digest)
        if path is not None:
            usable[digest] = path
    return usable


def pull_image(
    image_ref: str, rootfs_dir: str, arch: str, insecure: bool = False
) -> dict:
    """Pull an OCI/Docker image and extract all layers into *rootfs_dir*.

    The manifest is checked in the local cache first. If cached and
    every layer blob is present *and* re-hashes to its digest, the
    install runs entirely without network access. If the manifest is
    cached but some layers are missing or fail that check, only an auth
    token is fetched before downloading them again.

    Registry traffic uses verified HTTPS unless *insecure* is set. With
    *insecure* a custom registry is reached over HTTPS with certificate
    verification disabled, falling back to plain HTTP when the registry only
    speaks HTTP (Docker Hub stays verified-HTTPS regardless). When enforcing
    HTTPS, an untrusted certificate or an HTTP-only registry surfaces a
    RuntimeError pointing the user at ``--allow-insecure``.

    Returns ``{"manifest": ..., "image_config": ...}``. The caller is
    expected to persist these into ``containers/<name>/manifest.json``
    so `run`, `reset`, and `login` can later read image_config.
    """
    token = None
    base = None

    manifest, repo, image_config = load_manifest_cache(image_ref, arch)
    registry = parse_image_ref(image_ref)[0]

    if manifest is not None:
        # The hit proves which image this entry holds; record it when the
        # entry is an old one that never stored its own reference.
        annotate_manifest_cache(image_ref, arch)
        layers = manifest.get("layers", [])
        usable = _usable_cached_layers(layers)
        missing = sum(1 for layer in layers if layer["digest"] not in usable)
        if not missing:
            log_info(f"Image '{image_ref}' ({arch}) is cached.")
        else:
            log_info(f"Downloading {missing} missing "
                     f"layer(s) for '{image_ref}' ({arch})...")
            try:
                log_info(f"Authenticating with registry{auth_note()}...")
                token, base = get_auth_token(
                    repo, registry, insecure=insecure
                )
            except (urllib.error.URLError, OSError) as net_err:
                if isinstance(net_err, urllib.error.HTTPError):
                    if net_err.code in (401, 403):
                        raise RuntimeError(
                            auth_denied_msg(image_ref, net_err.code)
                        ) from net_err
                    if net_err.code == 404:
                        raise RuntimeError(
                            f"Image not found: '{image_ref}' does not "
                            f"exist on the registry."
                        ) from net_err
                log_error(f"{missing} of {len(layers)} layer(s) for "
                          f"'{image_ref}' ({arch}) are not in the local "
                          f"cache.")
                raise RuntimeError(f"Network error: {net_err}") from net_err
    else:
        try:
            manifest, token, repo, base = _resolve_single_manifest(
                image_ref, arch, insecure
            )
        except (urllib.error.URLError, OSError) as net_err:
            if isinstance(net_err, urllib.error.HTTPError):
                if net_err.code in (401, 403):
                    raise RuntimeError(
                        auth_denied_msg(image_ref, net_err.code)
                    ) from net_err
                if net_err.code == 404:
                    raise RuntimeError(
                        f"Image not found: '{image_ref}' does not exist "
                        f"on the registry."
                    ) from net_err
            log_error(f"No cached manifest found for '{image_ref}' ({arch}).")
            raise RuntimeError(f"Network error: {net_err}") from net_err
        cfg_digest = manifest.get("config", {}).get("digest", "")
        image_config = _fetch_config_blob(
            repo, cfg_digest, token, base, insecure
        )
        save_manifest_cache(image_ref, arch, manifest, repo, image_config)
        usable = _usable_cached_layers(manifest.get("layers", []))

    layers = manifest.get("layers", [])
    if not layers:
        raise RuntimeError(
            f"Manifest for '{image_ref}' contains no filesystem layers."
        )

    n_layers = len(layers)
    for i, layer in enumerate(layers):
        digest = layer["digest"]
        media_type = layer.get("mediaType", "")
        if "zstd" in media_type and not ZSTD_AVAILABLE:
            raise RuntimeError(
                unsupported_msg(f"Layer {i + 1}/{n_layers}")
                + " Try a different image tag that ships gzip-compressed "
                  "layers."
            )

        short_id = digest.split(":")[-1][:12]
        cached_path = usable.get(digest)
        if cached_path is not None:
            log_info(f"{short_id}: Layer {i + 1}/{n_layers} already cached, "
                     f"skipping download.")
            layer_path = cached_path
        else:
            size = layer.get("size", 0)
            size_str = f" ({fmt_size(size)})" if size else ""
            log_info(f"{short_id}: Downloading layer "
                     f"{i + 1}/{n_layers}{size_str}...")
            try:
                layer_path = download_blob(
                    repo, digest, token or "", base, insecure
                )
            except urllib.error.HTTPError as dl_err:
                if dl_err.code in (401, 403):
                    raise RuntimeError(
                        auth_denied_msg(image_ref, dl_err.code)
                    ) from dl_err
                raise

        log_info(f"{short_id}: Applying layer {i + 1}/{n_layers}...")
        apply_layer(layer_path, rootfs_dir)

    return {
        "manifest": manifest,
        "image_config": image_config,
    }
