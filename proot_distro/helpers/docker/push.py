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

# Architecture: The push pipeline.
#
#   1. Load the cached manifest + image_config produced by `build`.
#      Re-canonicalize the config and verify its digest matches
#      manifest.config.digest — guards against cache corruption.
#   2. Exchange PD_DOCKER_AUTH for a Bearer token with `pull,push` scope.
#   3. For each layer: HEAD-probe (skip if present), else
#      POST /v2/<repo>/blobs/uploads/ + PUT <location>?digest=<digest>
#      streaming from the cached blob through a progress reader.
#   4. Upload the image config blob.
#   5. PUT the manifest under the tag.

import hashlib
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.message import (
    C, is_quiet, log_info,
)
from proot_distro.constants import PROGRAM_NAME
from proot_distro.progress import (
    clear_bar, fmt_size,
)
from proot_distro.helpers.docker.cache import (
    layer_cache_path, load_manifest_cache,
)
from proot_distro.helpers.docker.media import (
    OCI_MANIFEST_MEDIA, canonical_json,
)
from proot_distro.helpers.docker.refs import parse_image_ref
from proot_distro.helpers.docker.transport import (
    auth_note,
    auth_opener,
    get_auth_token,
    push_denied_msg,
    registry_base_url,
    _ua,
)


def _resolve_upload_url(base: str, location: str) -> str:
    """Resolve the Location header from POST /v2/<repo>/blobs/uploads/."""
    if not location:
        raise RuntimeError(
            "Registry did not return an upload Location header."
        )
    if location.startswith(("http://", "https://")):
        return location
    if location.startswith("/"):
        return base + location
    return base.rstrip("/") + "/" + location


def _blob_exists(
    repo: str, digest: str, token: str, registry: str = "",
) -> bool:
    """Return True iff blob *digest* already exists on the registry."""
    base = registry_base_url(registry)
    url = f"{base}/v2/{repo}/blobs/{digest}"
    headers = {**_ua()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, method="HEAD", headers=headers)
    try:
        with auth_opener().open(req) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        if exc.code == 404:
            return False
        raise


class _ProgressReader:
    """File wrapper that draws an upload progress bar as read() runs."""

    def __init__(self, fh, total: int, label: str):
        self._fh = fh
        self.total = total
        self.sent = 0
        self._label = label
        self._tty = sys.stderr.isatty() and not is_quiet()
        self._last_shown = 0

    def read(self, size=-1):
        data = self._fh.read(size)
        self.sent += len(data)
        if self._tty and (self.sent - self._last_shown >= 262144
                          or len(data) == 0):
            self._last_shown = self.sent
            pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            if self.total:
                pct = min(self.sent * 100 // self.total, 100)
                bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
                line = (
                    f"\r{pfx}{self._label}: [{bar}] {pct:3d}%  "
                    f"{fmt_size(self.sent)} / "
                    f"{fmt_size(self.total)}\033[K{C['RST']}"
                )
            else:
                line = (
                    f"\r{pfx}{self._label}: "
                    f"{fmt_size(self.sent)} uploaded...\033[K{C['RST']}"
                )
            sys.stderr.write(line)
            sys.stderr.flush()
        return data


def _upload_blob_bytes(
    repo: str, digest: str, data: bytes, token: str,
    registry: str = "",
) -> None:
    """Upload a small in-memory blob (POST + monolithic PUT).

    Per the OCI Distribution spec the PUT body uses
    'application/octet-stream'; the manifest's mediaType field describes
    the *content* of the blob and is set separately when the manifest
    itself is uploaded.
    """
    base = registry_base_url(registry)
    headers = {**_ua()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    post_req = urllib.request.Request(
        f"{base}/v2/{repo}/blobs/uploads/",
        data=b"",
        method="POST",
        headers={**headers, "Content-Length": "0"},
    )
    with auth_opener().open(post_req) as resp:
        location = resp.headers.get("Location", "")
    put_url = _resolve_upload_url(base, location)
    sep = "&" if "?" in put_url else "?"
    put_url = f"{put_url}{sep}digest={urllib.parse.quote(digest, safe='')}"
    put_req = urllib.request.Request(
        put_url,
        data=data,
        method="PUT",
        headers={
            **headers,
            "Content-Type": "application/octet-stream",
            "Content-Length": str(len(data)),
        },
    )
    with auth_opener().open(put_req) as resp:
        if not 200 <= resp.status < 300:
            raise RuntimeError(
                f"Blob upload failed for {digest}: HTTP {resp.status}"
            )


def _upload_blob_file(
    repo: str, digest: str, file_path: str, token: str,
    registry: str = "", label: str = "",
) -> None:
    """Upload a blob from *file_path* (streamed, POST + monolithic PUT)."""
    base = registry_base_url(registry)
    headers = {**_ua()}
    if token:
        headers["Authorization"] = f"Bearer {token}"

    post_req = urllib.request.Request(
        f"{base}/v2/{repo}/blobs/uploads/",
        data=b"",
        method="POST",
        headers={**headers, "Content-Length": "0"},
    )
    with auth_opener().open(post_req) as resp:
        location = resp.headers.get("Location", "")
    put_url = _resolve_upload_url(base, location)
    sep = "&" if "?" in put_url else "?"
    put_url = f"{put_url}{sep}digest={urllib.parse.quote(digest, safe='')}"

    size = os.path.getsize(file_path)
    try:
        with open(file_path, "rb") as fh:
            reader = _ProgressReader(fh, size, label or digest[:19])
            put_req = urllib.request.Request(
                put_url,
                data=reader,
                method="PUT",
                headers={
                    **headers,
                    "Content-Type": "application/octet-stream",
                    "Content-Length": str(size),
                },
            )
            with auth_opener().open(put_req) as resp:
                if not 200 <= resp.status < 300:
                    raise RuntimeError(
                        f"Blob upload failed for {digest}: HTTP {resp.status}"
                    )
    finally:
        clear_bar()


def _put_manifest(
    repo: str, reference: str, body: bytes, media_type: str,
    token: str, registry: str = "",
) -> str:
    """PUT a manifest at <reference> (tag or digest). Returns the registry
    digest from the Docker-Content-Digest header, if provided."""
    base = registry_base_url(registry)
    url = f"{base}/v2/{repo}/manifests/{reference}"
    headers = {
        **_ua(),
        "Content-Type": media_type,
        "Content-Length": str(len(body)),
    }
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, data=body, method="PUT", headers=headers)
    with auth_opener().open(req) as resp:
        if not 200 <= resp.status < 300:
            raise RuntimeError(
                f"Manifest upload failed: HTTP {resp.status}"
            )
        return resp.headers.get("Docker-Content-Digest", "")


def _strip_private_keys(d: dict) -> dict:
    """Return a shallow copy of *d* without keys starting with '_'.

    `_get_manifest` stuffs the response Content-Type into `_ct` for
    internal use; that key must not be serialised back to the registry.
    """
    return {k: v for k, v in d.items() if not k.startswith("_")}


def push_image(image_ref: str, arch: str) -> dict:
    """Push a built image (resolved from the manifest cache) to its registry.

    The image must have been produced by `proot-distro build` under
    exactly this *image_ref* and *arch* — `build` stores the manifest
    in MANIFEST_CACHE_DIR and the layer + config blobs in
    LAYER_CACHE_DIR using the same digests we transmit here.
    """
    manifest, repo, image_config = load_manifest_cache(image_ref, arch)
    if manifest is None:
        raise RuntimeError(
            f"No cached manifest for '{image_ref}' ({arch}). Build image "
            f"first with: {PROGRAM_NAME} build -t {image_ref}"
        )

    layers = manifest.get("layers", [])
    if not layers:
        raise RuntimeError(
            f"Cached manifest for '{image_ref}' has no filesystem layers."
        )

    missing = [
        layer["digest"] for layer in layers
        if not os.path.isfile(layer_cache_path(layer["digest"]))
    ]
    if missing:
        raise RuntimeError(
            f"Cannot push '{image_ref}': {len(missing)} layer blob(s) are "
            f"missing from the local cache (first missing: {missing[0]}). "
            f"Rebuild the image to repopulate the cache."
        )

    registry, _, tag = parse_image_ref(image_ref)

    # Re-canonicalize the image config and verify the digest. The manifest
    # carries config.digest, which the registry verifies against the bytes
    # we PUT. Round-tripping the dict through json.dump+json.load preserves
    # all keys, so the canonical form is reproducible here.
    config_bytes = canonical_json(image_config)
    expected_cfg_digest = manifest.get("config", {}).get("digest", "")
    actual_cfg_digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
    if expected_cfg_digest != actual_cfg_digest:
        raise RuntimeError(
            f"Image config digest mismatch (cached manifest expects "
            f"{expected_cfg_digest}, regenerated bytes hash to "
            f"{actual_cfg_digest}). The local cache appears corrupted; "
            f"rebuild the image."
        )

    log_info(f"Authenticating with registry{auth_note()}...")
    try:
        token = get_auth_token(repo, registry, actions="pull,push")
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(push_denied_msg(image_ref, exc.code)) from exc
        raise

    n_layers = len(layers)
    bytes_uploaded = 0

    for i, layer in enumerate(layers):
        digest = layer["digest"]
        short_id = digest.split(":")[-1][:12]
        path = layer_cache_path(digest)
        size = os.path.getsize(path)

        try:
            if _blob_exists(repo, digest, token, registry):
                log_info(f"{short_id}: Layer {i + 1}/{n_layers} already "
                         f"exists on registry, skipping upload.")
                continue

            log_info(f"{short_id}: Uploading layer {i + 1}/{n_layers} "
                     f"({fmt_size(size)})...")
            _upload_blob_file(
                repo, digest, path, token, registry, label=short_id,
            )
            bytes_uploaded += size
        except urllib.error.HTTPError as exc:
            if exc.code in (401, 403):
                raise RuntimeError(
                    push_denied_msg(image_ref, exc.code)
                ) from exc
            raise

    cfg_short = expected_cfg_digest.split(":")[-1][:12]
    try:
        if _blob_exists(repo, expected_cfg_digest, token, registry):
            log_info(f"{cfg_short}: Image config already exists on "
                     f"registry, skipping upload.")
        else:
            log_info(f"{cfg_short}: Uploading image config "
                     f"({fmt_size(len(config_bytes))})...")
            _upload_blob_bytes(
                repo, expected_cfg_digest, config_bytes, token, registry,
            )
            bytes_uploaded += len(config_bytes)
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(push_denied_msg(image_ref, exc.code)) from exc
        raise

    manifest_media = manifest.get("mediaType") or OCI_MANIFEST_MEDIA
    manifest_bytes = canonical_json(_strip_private_keys(manifest))
    log_info(f"Uploading manifest for tag '{tag}' "
             f"({fmt_size(len(manifest_bytes))})...")
    try:
        registry_digest = _put_manifest(
            repo, tag, manifest_bytes, manifest_media, token, registry,
        )
    except urllib.error.HTTPError as exc:
        if exc.code in (401, 403):
            raise RuntimeError(push_denied_msg(image_ref, exc.code)) from exc
        raise
    bytes_uploaded += len(manifest_bytes)

    return {
        "manifest_digest": registry_digest,
        "bytes_uploaded": bytes_uploaded,
        "registry": registry or "docker.io",
        "repo": repo,
        "tag": tag,
    }
