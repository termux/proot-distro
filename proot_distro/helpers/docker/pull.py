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
import os
import urllib.error
import urllib.request

from proot_distro.compress import ZSTD_AVAILABLE, unsupported_msg
from proot_distro.message import log_info, log_error
from proot_distro.progress import fmt_size
from proot_distro.helpers.download import retry_http
from proot_distro.helpers.docker.cache import (
    annotate_manifest_cache,
    load_manifest_cache,
    manifest_layers,
    require_data_digest,
    save_manifest_cache,
    open_verified_layer,
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
    MAX_METADATA_BYTES,
    decode_json_object,
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
            # Metadata, held whole in memory and subscripted below; how
            # much of it there is is the registry's choice, so one byte
            # past the ceiling is the refusal.
            body = resp.read(MAX_METADATA_BYTES + 1)
            ct = resp.headers.get("Content-Type", "")
        if len(body) > MAX_METADATA_BYTES:
            raise RuntimeError(
                f"Manifest for '{repo}' is larger than "
                f"{MAX_METADATA_BYTES} bytes; refusing to read it."
            )
        return body, ct

    body, ct = retry_http(_attempt, what=f"Fetching manifest {ref}")
    # A reference containing ':' is a digest, not a tag: the registry's
    # answer is content-addressed, so it has to hash to what we asked
    # for. This is the only check standing between a hostile mirror (or
    # an --allow-insecure MITM) and an arbitrary layer list.
    if ":" in ref:
        require_data_digest(body, ref, what=f"Manifest for '{repo}'")
    data = decode_json_object(body, f"Fetching manifest {ref}")
    # Prefer the Content-Type header; fall back to the mediaType field.
    media = data.get("mediaType", "")
    data["_ct"] = ct.split(";")[0].strip() or (
        media if isinstance(media, str) else ""
    )
    return data


def _entry_platform(entry):
    """Return an index entry's platform object, or None when it has none.

    Both the entry and its platform are whatever the registry sent, and
    an index whose entries are strings (or whose platform is a list) used
    to end the pull in an AttributeError partway down _pick_platform. An
    entry that cannot be read simply does not match, which is the same
    outcome as one describing another architecture. An entry that is a
    proper object with no platform at all still answers {}, as it always
    did: it matches nothing either, but it is listed among the platforms
    the image does offer.
    """
    if not isinstance(entry, dict):
        return None
    plat = entry.get("platform", {})
    return plat if isinstance(plat, dict) else None


def _pick_platform(
    entries: list, arch: str, variant: str, image_ref: str
) -> dict:
    """Find the manifest list entry matching arch (and optionally variant)."""
    # Exact match first (arch + non-empty variant must match).
    for entry in entries:
        plat = _entry_platform(entry)
        if plat is None:
            continue
        if plat.get("os", "linux") != "linux":
            continue
        if plat.get("architecture") != arch:
            continue
        if variant and plat.get("variant", "") not in (variant, ""):
            continue
        return entry

    # Variant-agnostic fallback.
    for entry in entries:
        plat = _entry_platform(entry)
        if plat is None:
            continue
        if (plat.get("os", "linux") == "linux"
                and plat.get("architecture") == arch):
            return entry

    available = []
    for e in entries:
        plat = _entry_platform(e)
        if plat is None or plat.get("os", "linux") != "linux":
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
        entries = manifest.get("manifests", [])
        if not isinstance(entries, list):
            raise RuntimeError(
                f"Manifest index for '{image_ref}' is malformed: "
                f"'manifests' is not a list."
            )
        target = _pick_platform(
            entries, docker_arch, docker_variant, image_ref,
        )
        digest = target.get("digest")
        if not isinstance(digest, str) or not digest:
            raise RuntimeError(
                f"Manifest index for '{image_ref}' names the {arch} image "
                f"with no usable digest."
            )
        log_info(f"Fetching {arch} manifest...")
        manifest = _get_manifest(repo, digest, token, base, insecure)

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
            # A config blob is metadata: parsed whole, never streamed,
            # so its size is the registry's choice of allocation.
            with opener(insecure).open(req) as resp:
                data = resp.read(MAX_METADATA_BYTES + 1)
            if len(data) > MAX_METADATA_BYTES:
                raise RuntimeError(
                    f"Image config blob is larger than "
                    f"{MAX_METADATA_BYTES} bytes; refusing to read it."
                )
            return data

        body = retry_http(_attempt, what="Fetching image config")
    except RuntimeError:
        # Too large is a refusal, not a degraded config: it is the one
        # failure here that says something about the blob rather than
        # about reaching it.
        raise
    except Exception:
        return {}

    require_data_digest(body, cfg_digest, what="Image config blob")
    try:
        config = json.loads(body)
    except (ValueError, UnicodeDecodeError):
        return {}
    # `run` and `login` read Entrypoint/Cmd/Env out of this, and it is
    # persisted into the manifest cache; a document that is not an object
    # answers no such question, so it is the same as not having one.
    return config if isinstance(config, dict) else {}


def _usable_cached_layers(layers: list) -> dict:
    """Return ``{digest: fd}`` for every layer blob already usable.

    Usable means present *and* hashing to its own digest. A blob that
    fails is dropped by open_verified_layer(), so it is simply absent
    here and gets downloaded again like any other missing layer.

    Deciding this once per pull, before the first apply, is what keeps
    the cost at one hash per blob: the "is it all cached?" question and
    the per-layer "download or reuse?" question read the same answer.

    The descriptors are held open for the rest of the pull, which is
    what makes that one hash count for the apply as well — the bytes
    extracted are the ones hashed here, not whatever the name refers to
    by then. pull_image closes them.
    """
    usable = {}
    for layer in layers:
        digest = layer.get("digest")
        if not digest or digest in usable:
            continue
        fd = open_verified_layer(digest)
        if fd is not None:
            usable[digest] = fd
    return usable


def _close_all(fds) -> None:
    """Close every descriptor in *fds*, ignoring failures."""
    for fd in fds:
        try:
            os.close(fd)
        except OSError:
            pass


def pull_image(
    image_ref: str, rootfs_fd: int, arch: str, insecure: bool = False
) -> dict:
    """Pull an OCI/Docker image into the tree *rootfs_fd* names.

    The rootfs is a **descriptor**, not a path: `install` validates
    containers/<name>/rootfs with an O_NOFOLLOW walk and hands the
    result straight down, so nothing between that check and the last
    member written resolves the name a second time.

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
    # Descriptors on verified cached blobs, held for the whole pull so the
    # hash taken below is the one the apply reads through; closed in the
    # finally, on every path out including a failed download.
    usable: dict = {}

    manifest, repo, image_config = load_manifest_cache(image_ref, arch)
    registry = parse_image_ref(image_ref)[0]
    try:
        return _pull_layers(
            image_ref, rootfs_fd, arch, insecure,
            manifest, repo, image_config, registry, token, base, usable,
        )
    finally:
        _close_all(usable.values())


def _pull_layers(image_ref, rootfs_fd, arch, insecure,
                 manifest, repo, image_config, registry, token, base, usable):
    """pull_image's body, with the caller owning the descriptor cleanup."""

    if manifest is not None:
        # The hit proves which image this entry holds; record it when the
        # entry is an old one that never stored its own reference.
        annotate_manifest_cache(image_ref, arch)
        layers = manifest_layers(manifest, image_ref)
        usable.update(_usable_cached_layers(layers))
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
        config = manifest.get("config", {})
        cfg_digest = config.get("digest", "") if isinstance(
            config, dict
        ) else ""
        if not isinstance(cfg_digest, str):
            cfg_digest = ""
        image_config = _fetch_config_blob(
            repo, cfg_digest, token, base, insecure
        )
        save_manifest_cache(image_ref, arch, manifest, repo, image_config)
        usable.update(
            _usable_cached_layers(manifest_layers(manifest, image_ref))
        )

    layers = manifest_layers(manifest, image_ref)
    if not layers:
        raise RuntimeError(
            f"Manifest for '{image_ref}' contains no filesystem layers."
        )

    n_layers = len(layers)
    for i, layer in enumerate(layers):
        # manifest_layers has vouched for the digest; the rest of a
        # descriptor is still the registry's to shape, and both of these
        # are only ever read for a message or a substring test.
        digest = layer["digest"]
        media_type = layer.get("mediaType", "")
        if not isinstance(media_type, str):
            media_type = ""
        if "zstd" in media_type and not ZSTD_AVAILABLE:
            raise RuntimeError(
                unsupported_msg(f"Layer {i + 1}/{n_layers}")
                + " Try a different image tag that ships gzip-compressed "
                  "layers."
            )

        short_id = digest.split(":")[-1][:12]
        # A cached descriptor belongs to `usable` and is closed with it —
        # the same layer may be listed twice, and extraction rewinds — while
        # a freshly downloaded one is this iteration's to close.
        layer_fd = usable.get(digest)
        owned = layer_fd is None
        if not owned:
            log_info(f"{short_id}: Layer {i + 1}/{n_layers} already cached, "
                     f"skipping download.")
        else:
            size = layer.get("size", 0)
            size_str = (f" ({fmt_size(size)})"
                        if isinstance(size, int) and size > 0 else "")
            log_info(f"{short_id}: Downloading layer "
                     f"{i + 1}/{n_layers}{size_str}...")
            try:
                layer_fd = download_blob(
                    repo, digest, token or "", base, insecure
                )
            except urllib.error.HTTPError as dl_err:
                if dl_err.code in (401, 403):
                    raise RuntimeError(
                        auth_denied_msg(image_ref, dl_err.code)
                    ) from dl_err
                raise

        log_info(f"{short_id}: Applying layer {i + 1}/{n_layers}...")
        try:
            apply_layer(layer_fd, rootfs_fd, digest=digest)
        finally:
            if owned:
                _close_all((layer_fd,))

    return {
        "manifest": manifest,
        "image_config": image_config,
    }
