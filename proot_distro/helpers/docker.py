#
# Proot-Distro - manage proot containers on Termux.
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

# Architecture: Pure-Python OCI/Docker registry client. Pulls images from
# Docker Hub (or any OCI-compatible registry) without spawning external
# processes. Manifest and layer data are cached locally to support fully
# offline re-installs. Authentication tokens are fetched on demand; bearer
# tokens are stripped on cross-host redirects because CDN pre-signed URLs
# reject them with HTTP 400.

import hashlib
import json
import os
import re
import shutil
import stat
import sys
import tarfile
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.constants import (
    LAYER_CACHE_DIR,
    MANIFEST_CACHE_DIR,
    PROGRAM_VERSION,
)
from proot_distro.colors import C, msg
from proot_distro.helpers.download import fmt_size

_REGISTRY_URL = "https://registry-1.docker.io"
_AUTH_URL = "https://auth.docker.io/token"

_MANIFEST_LIST_TYPES = frozenset({
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.index.v1+json",
})

# Accepted manifest media types, ordered by preference (index first).
_ACCEPT_HEADER = ", ".join([
    "application/vnd.oci.image.index.v1+json",
    "application/vnd.docker.distribution.manifest.list.v2+json",
    "application/vnd.oci.image.manifest.v1+json",
    "application/vnd.docker.distribution.manifest.v2+json",
])

# proot-distro arch name → (Docker architecture, optional variant)
_ARCH_TO_DOCKER = {
    "aarch64": ("arm64",   ""),
    "arm":     ("arm",     "v7"),
    "i686":    ("386",     ""),
    "x86_64":  ("amd64",  ""),
    "riscv64": ("riscv64", ""),
}


# ---------------------------------------------------------------------------
# Image reference utilities
# ---------------------------------------------------------------------------

def parse_image_ref(image_ref: str) -> tuple:
    """Parse an image reference into (registry, repo, tag).

    Docker Hub images (no registry host):
      'ubuntu'           → ('', 'library/ubuntu', 'latest')
      'ubuntu:24.04'     → ('', 'library/ubuntu', '24.04')
      'myuser/img:1.0'   → ('', 'myuser/img', '1.0')

    Custom registry images (host contains a dot or colon):
      'ghcr.io/foo/bar:latest' → ('ghcr.io', 'foo/bar', 'latest')
    """
    # Detect a registry host: first path component contains '.' or ':'
    parts = image_ref.split("/", 1)
    if len(parts) == 2 and ("." in parts[0] or ":" in parts[0]):
        registry = parts[0]
        remainder = parts[1]
    else:
        registry = ""
        remainder = image_ref

    if ":" in remainder:
        name, tag = remainder.rsplit(":", 1)
    else:
        name, tag = remainder, "latest"

    if not registry:
        repo = name if "/" in name else f"library/{name}"
    else:
        repo = name

    return registry, repo, tag


def derive_alias(image_ref: str) -> str:
    """Derive a short local alias from an image reference.

    'ubuntu:24.04'         → 'ubuntu'
    'myuser/img:tag'       → 'img'
    'ghcr.io/foo/bar:tag'  → 'bar'
    """
    name = image_ref.split(":")[0]
    return name.split("/")[-1]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _layer_cache_path(digest: str) -> str:
    return os.path.join(LAYER_CACHE_DIR, digest.replace(":", "_"))


def _manifest_cache_path(image_ref: str, arch: str) -> str:
    safe = re.sub(r"[^\w._-]", "_", f"{image_ref}_{arch}")
    return os.path.join(MANIFEST_CACHE_DIR, safe + ".json")


def _save_manifest_cache(
    image_ref: str, arch: str,
    manifest: dict, repo: str, image_config: dict,
) -> None:
    path = _manifest_cache_path(image_ref, arch)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(
            {"manifest": manifest, "repo": repo, "image_config": image_config},
            fh,
        )
    os.replace(tmp, path)


def _load_manifest_cache(image_ref: str, arch: str):
    """Return (manifest, repo, image_config) from cache, or None on miss."""
    try:
        with open(_manifest_cache_path(image_ref, arch)) as fh:
            data = json.load(fh)
        return data["manifest"], data["repo"], data.get("image_config", {})
    except (OSError, json.JSONDecodeError, KeyError):
        return None, None, {}


def _all_layers_cached(layers: list) -> bool:
    return all(
        os.path.isfile(_layer_cache_path(layer["digest"])) for layer in layers
    )


# ---------------------------------------------------------------------------
# Registry API helpers
# ---------------------------------------------------------------------------

def _ua() -> dict:
    return {"User-Agent": f"proot-distro/{PROGRAM_VERSION}"}


class _AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip the Authorization header when following a cross-host redirect.

    Docker Hub blob endpoints redirect to CDN pre-signed URLs. Those CDN
    hosts return HTTP 400 when they receive a Bearer token. Python's default
    redirect handler forwards all headers unchanged, so we override it to
    drop Authorization whenever the redirect target host differs from the
    source host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        orig_host = urllib.parse.urlparse(req.full_url).netloc
        new_host = urllib.parse.urlparse(newurl).netloc
        if orig_host != new_host:
            new_req.headers.pop("Authorization", None)
        return new_req


_auth_stripping_opener = urllib.request.build_opener(_AuthStrippingRedirectHandler)


def _parse_bearer_challenge(header_value: str) -> dict:
    """Return the key=value pairs from a Bearer WWW-Authenticate header."""
    return dict(re.findall(r'(\w+)="([^"]*)"', header_value))


def _get_auth_token(repo: str, registry: str = "") -> str:
    """Obtain a pull token for *repo* from the appropriate auth endpoint.

    Docker Hub uses a well-known auth endpoint. For any other registry
    (e.g. ghcr.io) the Bearer realm is discovered by probing /v2/ and
    following the standard OCI auth challenge, allowing public images to
    be pulled with an anonymous token.
    """
    if not registry:
        url = (
            f"{_AUTH_URL}?service=registry.docker.io"
            f"&scope=repository:{repo}:pull"
        )
        req = urllib.request.Request(url, headers=_ua())
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        return data.get("token") or data.get("access_token", "")

    # Custom registry: probe /v2/ to discover the Bearer realm, then fetch
    # an anonymous pull token. Registries that serve public images (e.g.
    # ghcr.io) still require this token dance — they always return 401 on
    # unauthenticated requests and embed the token endpoint in the challenge.
    probe_req = urllib.request.Request(
        f"https://{registry}/v2/", headers=_ua()
    )
    try:
        with urllib.request.urlopen(probe_req) as resp:
            resp.read()
        return ""  # registry is wide open; no token required
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        www_auth = exc.headers.get("WWW-Authenticate", "")
        if not www_auth.lower().startswith("bearer "):
            return ""  # unsupported auth scheme
        params = _parse_bearer_challenge(www_auth.split(" ", 1)[1])
        realm = params.get("realm", "")
        if not realm:
            return ""
        service = params.get("service", "")
        qs_parts = []
        if service:
            qs_parts.append(f"service={urllib.parse.quote(service, safe='')}")
        qs_parts.append(f"scope=repository:{repo}:pull")
        sep = "&" if "?" in realm else "?"
        token_req = urllib.request.Request(
            f"{realm}{sep}{'&'.join(qs_parts)}", headers=_ua()
        )
        with urllib.request.urlopen(token_req) as resp:
            data = json.loads(resp.read())
        return data.get("token") or data.get("access_token", "")


def _registry_base_url(registry: str) -> str:
    if registry:
        return f"https://{registry}"
    return _REGISTRY_URL


def _get_manifest(
    repo: str, ref: str, token: str, registry: str = ""
) -> dict:
    base = _registry_base_url(registry)
    url = f"{base}/v2/{repo}/manifests/{ref}"
    headers = {**_ua(), "Accept": _ACCEPT_HEADER}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req) as resp:
        body = resp.read()
        ct = resp.headers.get("Content-Type", "")
    data = json.loads(body)
    # Prefer Content-Type header; fall back to the mediaType body field.
    data["_ct"] = ct.split(";")[0].strip() or data.get("mediaType", "")
    return data


def _resolve_single_manifest(image_ref: str, arch: str) -> tuple:
    """Return (single_image_manifest, token, repo, registry) for the arch."""
    registry, repo, tag = parse_image_ref(image_ref)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
        f"{C['CYAN']}Authenticating with registry...{C['RST']}")
    token = _get_auth_token(repo, registry)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
        f"{C['CYAN']}Fetching manifest for '{image_ref}'...{C['RST']}")
    manifest = _get_manifest(repo, tag, token, registry)

    if manifest["_ct"] in _MANIFEST_LIST_TYPES or "manifests" in manifest:
        docker_arch, docker_variant = _ARCH_TO_DOCKER.get(arch, (arch, ""))
        target = _pick_platform(
            manifest.get("manifests", []),
            docker_arch,
            docker_variant,
            image_ref,
        )
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
            f"{C['CYAN']}Fetching {arch} manifest...{C['RST']}")
        manifest = _get_manifest(repo, target["digest"], token, registry)

    return manifest, token, repo, registry


def _pick_platform(
    entries: list, arch: str, variant: str, image_ref: str
) -> dict:
    """Find the manifest list entry matching arch (and optionally variant)."""
    # First pass: exact match (arch + non-empty variant must match).
    for entry in entries:
        plat = entry.get("platform", {})
        if plat.get("os", "linux") != "linux":
            continue
        if plat.get("architecture") != arch:
            continue
        if variant and plat.get("variant", "") not in (variant, ""):
            continue
        return entry

    # Second pass: arch-only (variant-agnostic fallback).
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
        f"Available Linux platforms: {', '.join(available) or 'none'}"
    )


def _fetch_config_blob(
    repo: str, cfg_digest: str, token: str, registry: str = ""
) -> dict:
    """Fetch the image config JSON blob; return parsed dict (empty on error)."""
    if not cfg_digest:
        return {}
    try:
        base = _registry_base_url(registry)
        url = f"{base}/v2/{repo}/blobs/{cfg_digest}"
        headers = {**_ua()}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        req = urllib.request.Request(url, headers=headers)
        with _auth_stripping_opener.open(req) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Layer download and application
# ---------------------------------------------------------------------------

def _download_blob(
    repo: str, digest: str, token: str, registry: str = ""
) -> str:
    """Download a blob to the layer cache; return the local file path.

    Computes the SHA-256 of the blob while it streams to disk and verifies
    it against the expected *digest* before promoting the temp file to its
    final location. The cache therefore only ever contains intact layers.
    """
    dest = _layer_cache_path(digest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest):
        return dest

    base = _registry_base_url(registry)
    url = f"{base}/v2/{repo}/blobs/{digest}"
    headers = {**_ua()}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(url, headers=headers)
    tmp = dest + ".tmp"
    use_tty = sys.stderr.isatty()

    if ":" not in digest:
        raise RuntimeError(f"Malformed layer digest '{digest}'.")
    algo, expected_hex = digest.split(":", 1)
    if algo.lower() != "sha256":
        raise RuntimeError(
            f"Unsupported layer digest algorithm '{algo}' (only sha256 "
            f"is supported)."
        )
    hasher = hashlib.sha256()

    try:
        with _auth_stripping_opener.open(req) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                hasher.update(chunk)
                downloaded += len(chunk)
                if use_tty:
                    pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                    if total:
                        pct = downloaded * 100 // total
                        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
                        line = (f"\r{pfx}[{bar}] {pct:3d}%"
                                f"  {fmt_size(downloaded)}"
                                f" / {fmt_size(total)}\033[K{C['RST']}")
                    else:
                        line = (f"\r{pfx}"
                                f"{fmt_size(downloaded)} downloaded..."
                                f"\033[K{C['RST']}")
                    sys.stderr.write(line)
                    sys.stderr.flush()
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

        actual_hex = hasher.hexdigest()
        if actual_hex != expected_hex.lower():
            raise RuntimeError(
                f"Layer integrity check failed for digest '{digest}': "
                f"expected {expected_hex}, got {actual_hex}."
            )

        os.replace(tmp, dest)
    except BaseException:
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
    return dest


class _ByteCounter:
    """Thin file wrapper that counts raw bytes passing through read()."""

    def __init__(self, fh):
        self._fh = fh
        self.count = 0

    def read(self, size=-1):
        data = self._fh.read(size)
        self.count += len(data)
        return data

    def readinto(self, buf):
        n = self._fh.readinto(buf)
        self.count += n
        return n

    def __getattr__(self, name):
        return getattr(self._fh, name)


def _remove_fstree(path: str) -> None:
    """Remove a file, symlink, or directory tree; ignore all errors."""
    try:
        if os.path.isdir(path) and not os.path.islink(path):
            shutil.rmtree(path, ignore_errors=True)
        else:
            os.remove(path)
    except OSError:
        pass


def _apply_layer(layer_path: str, rootfs_dir: str) -> None:
    """Apply one OCI/Docker layer (gzipped tar) onto rootfs_dir.

    Whiteout semantics (OCI image spec §6.1.2):
      .wh..wh..opq   opaque whiteout — delete all parent dir contents
      .wh.<name>     regular whiteout — delete sibling <name>

    Hard links are copied rather than linked to keep the rootfs
    self-contained. Block/character devices and FIFOs are silently skipped.

    Progress is tracked in compressed bytes consumed (via _ByteCounter) so
    the denominator is os.path.getsize() — instant — and no upfront scan of
    the archive is needed.
    """
    use_tty = sys.stderr.isatty()
    total_size = os.path.getsize(layer_path)

    def _show(counter: _ByteCounter) -> None:
        if not use_tty or not total_size:
            return
        done = counter.count
        pct = min(done * 100 // total_size, 100)
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        sys.stderr.write(
            f"\r{pfx}[{bar}] {pct:3d}%  "
            f"{fmt_size(done)} / {fmt_size(total_size)}\033[K{C['RST']}"
        )
        sys.stderr.flush()

    deferred_links: list = []  # list of (dest, src_path)
    deferred_dirs: list = []   # list of (path, mtime) — set after all writes

    with open(layer_path, "rb") as raw_fh:
        counter = _ByteCounter(raw_fh)
        with tarfile.open(fileobj=counter, mode="r|*") as tf:
            for m in tf:
                name = m.name.lstrip("/").rstrip("/")
                if not name or name == ".":
                    _show(counter)
                    continue

                parts = name.split("/")
                basename = parts[-1]
                parent = (os.path.join(rootfs_dir, *parts[:-1])
                          if len(parts) > 1 else rootfs_dir)
                dest = os.path.join(rootfs_dir, name)

                # Opaque whiteout: clear everything inside the parent directory.
                if basename == ".wh..wh..opq":
                    if os.path.isdir(parent):
                        for entry in os.listdir(parent):
                            _remove_fstree(os.path.join(parent, entry))
                    _show(counter)
                    continue

                # Regular whiteout: delete the named sibling.
                if basename.startswith(".wh."):
                    _remove_fstree(os.path.join(parent, basename[4:]))
                    _show(counter)
                    continue

                # Skip device files and FIFOs.
                if m.isblk() or m.ischr() or m.isfifo():
                    _show(counter)
                    continue

                os.makedirs(parent, exist_ok=True)

                if m.isdir():
                    os.makedirs(dest, exist_ok=True)
                    try:
                        os.chmod(dest, stat.S_IMODE(m.mode) | stat.S_IRWXU)
                    except OSError:
                        pass
                    deferred_dirs.append((dest, m.mtime))

                elif m.issym():
                    if os.path.lexists(dest):
                        os.remove(dest)
                    os.symlink(m.linkname, dest)
                    try:
                        os.utime(dest, (m.mtime, m.mtime), follow_symlinks=False)
                    except OSError:
                        pass

                elif m.islnk():
                    src = os.path.join(rootfs_dir, m.linkname.lstrip("/"))
                    deferred_links.append((dest, src))
                    _show(counter)
                    continue

                elif m.isreg():
                    fobj = tf.extractfile(m)
                    if fobj is None:
                        _show(counter)
                        continue
                    if os.path.lexists(dest):
                        try:
                            os.remove(dest)
                        except OSError:
                            pass
                    try:
                        with open(dest, "wb") as out:
                            shutil.copyfileobj(fobj, out)
                        try:
                            os.chmod(dest, stat.S_IMODE(m.mode))
                        except OSError:
                            pass
                        try:
                            os.utime(dest, (m.mtime, m.mtime))
                        except OSError:
                            pass
                    finally:
                        fobj.close()

                _show(counter)

    # All regular files are written; now copy hard links.
    # shutil.copy2 preserves the source mtime, which was already set above.
    for dest, src in deferred_links:
        if os.path.lexists(dest):
            try:
                os.remove(dest)
            except OSError:
                pass
        if os.path.isfile(src):
            try:
                shutil.copy2(src, dest)
            except OSError:
                pass

    # Apply directory timestamps last. Writing files into a directory
    # updates its mtime, so this must happen after all file writes.
    for path, mtime in reversed(deferred_dirs):
        try:
            os.utime(path, (mtime, mtime))
        except OSError:
            pass

    if use_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pull_image(image_ref: str, rootfs_dir: str, arch: str) -> dict:
    """Pull an OCI/Docker image and extract all layers into rootfs_dir.

    The manifest is checked in the local cache first. If cached and all layers
    are present, the install runs entirely without network access. If the
    manifest is cached but some layers are missing, only an auth token is
    fetched before downloading the missing layers. The manifest cache is
    populated on the first successful online pull.

    Returns a metadata dict with 'name', 'version', 'description', 'env',
    'manifest', and 'image_config' keys.
    """
    token = None

    # --- Check manifest cache first ---
    manifest, repo, image_config = _load_manifest_cache(image_ref, arch)

    registry = parse_image_ref(image_ref)[0]

    if manifest is not None:
        layers = manifest.get("layers", [])
        if _all_layers_cached(layers):
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                f"{C['CYAN']}Manifest and all layers are cached — "
                f"skipping network for '{image_ref}' ({arch}).{C['RST']}")
        else:
            missing = sum(
                1 for layer in layers
                if not os.path.isfile(_layer_cache_path(layer["digest"]))
            )
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                f"{C['CYAN']}Manifest cached — downloading {missing} missing "
                f"layer(s) for '{image_ref}' ({arch})...{C['RST']}")
            try:
                msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                    f"{C['CYAN']}Authenticating with registry...{C['RST']}")
                token = _get_auth_token(repo, registry)
            except (urllib.error.URLError, OSError) as net_err:
                if (isinstance(net_err, urllib.error.HTTPError)
                        and net_err.code == 404):
                    raise RuntimeError(
                        f"Image not found: '{image_ref}' does not exist "
                        f"on the registry."
                    ) from net_err
                raise RuntimeError(
                    f"Network error: {net_err}\n"
                    f"{missing} of {len(layers)} layer(s) for '{image_ref}'"
                    f" ({arch}) are not in the local cache. "
                    f"Connect to the internet and retry."
                ) from net_err
    else:
        # No cached manifest — resolve from the registry.
        try:
            manifest, token, repo, registry = _resolve_single_manifest(
                image_ref, arch
            )
        except (urllib.error.URLError, OSError) as net_err:
            if (isinstance(net_err, urllib.error.HTTPError)
                    and net_err.code == 404):
                raise RuntimeError(
                    f"Image not found: '{image_ref}' does not exist "
                    f"on the registry."
                ) from net_err
            raise RuntimeError(
                f"Network error: {net_err}\n"
                f"No cached manifest found for '{image_ref}' ({arch}). "
                f"Connect to the internet and retry."
            ) from net_err
        cfg_digest = manifest.get("config", {}).get("digest", "")
        image_config = _fetch_config_blob(repo, cfg_digest, token, registry)
        _save_manifest_cache(image_ref, arch, manifest, repo, image_config)

    layers = manifest.get("layers", [])
    if not layers:
        raise RuntimeError(
            f"Manifest for '{image_ref}' contains no filesystem layers."
        )

    # --- Download (if needed) and apply each layer ---
    n_layers = len(layers)
    for i, layer in enumerate(layers):
        digest = layer["digest"]
        media_type = layer.get("mediaType", "")
        if "zstd" in media_type:
            raise RuntimeError(
                f"Layer {i + 1}/{n_layers} uses zstd compression which is "
                "not supported by Python's tarfile module. "
                "Try a different image tag that ships gzip-compressed layers."
            )

        short_id = digest.split(":")[-1][:12]
        cached_path = _layer_cache_path(digest)
        if os.path.isfile(cached_path):
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"{short_id}: Layer {i + 1}/{n_layers} already cached, "
                f"skipping download.{C['RST']}")
            layer_path = cached_path
        else:
            size = layer.get("size", 0)
            size_str = f" ({fmt_size(size)})" if size else ""
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"{short_id}: Downloading layer "
                f"{i + 1}/{n_layers}{size_str}...{C['RST']}")
            layer_path = _download_blob(repo, digest, token or "", registry)

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"{short_id}: Applying layer {i + 1}/{n_layers}...{C['RST']}")
        _apply_layer(layer_path, rootfs_dir)

    # --- Build return metadata from image config labels ---
    _, _, tag = parse_image_ref(image_ref)
    image_name = repo.split("/")[-1].capitalize()
    img_cfg: dict = image_config.get("config") or {}
    labels: dict = img_cfg.get("Labels") or {}
    version_str = str(
        labels.get("org.opencontainers.image.version")
        or labels.get("version")
        or (tag if tag != "latest" else "")
    )
    description = str(
        labels.get("org.opencontainers.image.description")
        or f"Installed from Docker Hub: {image_ref}"
    )
    # Env is a list of "KEY=VALUE" strings defined by the image author.
    image_env: list = [
        e for e in (img_cfg.get("Env") or [])
        if isinstance(e, str) and "=" in e
    ]
    return {
        "name": image_name,
        "version": version_str,
        "description": description,
        "env": image_env,
        "manifest": manifest,
        "image_config": image_config,
    }
