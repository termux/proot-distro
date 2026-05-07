"""
Proot-Distro - manage proot containers on Termux.

Created by Sylirre <sylirre@termux.dev> for Termux project.
Development assisted by Claude Code (https://claude.ai/code).

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""
"""Pure-Python OCI registry client for pulling images from Docker Hub."""
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

from proot_distro.constants import DOWNLOAD_CACHE_DIR, PROGRAM_VERSION
from proot_distro.colors import C, msg

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
    """Parse an image reference into (repo, tag).

    'ubuntu'          → ('library/ubuntu', 'latest')
    'ubuntu:24.04'    → ('library/ubuntu', '24.04')
    'myuser/img:1.0'  → ('myuser/img', '1.0')
    """
    if ":" in image_ref:
        name, tag = image_ref.rsplit(":", 1)
    else:
        name, tag = image_ref, "latest"
    repo = name if "/" in name else f"library/{name}"
    return repo, tag


def derive_alias(image_ref: str) -> str:
    """Derive a short local alias from an image reference.

    'ubuntu:24.04'    → 'ubuntu'
    'myuser/img:tag'  → 'img'
    """
    name = image_ref.split(":")[0]
    return name.split("/")[-1]


# ---------------------------------------------------------------------------
# Cache helpers
# ---------------------------------------------------------------------------

def _layer_cache_path(digest: str) -> str:
    return os.path.join(DOWNLOAD_CACHE_DIR, "docker-layers", digest.replace(":", "_"))


def _manifest_cache_path(image_ref: str, arch: str) -> str:
    safe = re.sub(r"[^\w._-]", "_", f"{image_ref}_{arch}")
    return os.path.join(DOWNLOAD_CACHE_DIR, "docker-manifests", safe + ".json")


def _save_manifest_cache(image_ref: str, arch: str,
                         manifest: dict, repo: str, image_config: dict) -> None:
    path = _manifest_cache_path(image_ref, arch)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"manifest": manifest, "repo": repo, "image_config": image_config}, fh)
    os.replace(tmp, path)


def _load_manifest_cache(image_ref: str, arch: str):
    """Return (manifest, repo, image_config) from cache, or (None, None, {}) on miss."""
    try:
        with open(_manifest_cache_path(image_ref, arch)) as fh:
            data = json.load(fh)
        return data["manifest"], data["repo"], data.get("image_config", {})
    except (OSError, json.JSONDecodeError, KeyError):
        return None, None, {}


def _all_layers_cached(layers: list) -> bool:
    return all(os.path.isfile(_layer_cache_path(l["digest"])) for l in layers)


# ---------------------------------------------------------------------------
# Registry API helpers
# ---------------------------------------------------------------------------

def _ua() -> dict:
    return {"User-Agent": f"proot-distro/{PROGRAM_VERSION}"}


class _AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Drop the Authorization header when following a cross-host redirect.

    Docker Hub's blob endpoints redirect to CDN pre-signed URLs.  Those CDN
    hosts reject requests that carry a Bearer token with HTTP 400.  Python's
    default redirect handler forwards all headers unchanged, so we override it
    to strip Authorization whenever the target host differs from the source.
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


def _get_auth_token(repo: str) -> str:
    url = (f"{_AUTH_URL}?service=registry.docker.io"
           f"&scope=repository:{repo}:pull")
    req = urllib.request.Request(url, headers=_ua())
    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())
    return data.get("token") or data.get("access_token", "")


def _get_manifest(repo: str, ref: str, token: str) -> dict:
    url = f"{_REGISTRY_URL}/v2/{repo}/manifests/{ref}"
    req = urllib.request.Request(url, headers={
        **_ua(),
        "Authorization": f"Bearer {token}",
        "Accept": _ACCEPT_HEADER,
    })
    with urllib.request.urlopen(req) as resp:
        body = resp.read()
        ct = resp.headers.get("Content-Type", "")
    data = json.loads(body)
    # Prefer the Content-Type header; fall back to the mediaType body field.
    data["_ct"] = ct.split(";")[0].strip() or data.get("mediaType", "")
    return data


def _resolve_single_manifest(image_ref: str, arch: str) -> tuple:
    """Return (single_image_manifest, token, repo) for the requested arch."""
    repo, tag = parse_image_ref(image_ref)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
        f"{C['CYAN']}Authenticating with Docker Hub...{C['RST']}")
    token = _get_auth_token(repo)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
        f"{C['CYAN']}Fetching manifest for '{image_ref}'...{C['RST']}")
    manifest = _get_manifest(repo, tag, token)

    if manifest["_ct"] in _MANIFEST_LIST_TYPES or "manifests" in manifest:
        docker_arch, docker_variant = _ARCH_TO_DOCKER.get(arch, (arch, ""))
        target = _pick_platform(
            manifest.get("manifests", []), docker_arch, docker_variant, image_ref)
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
            f"{C['CYAN']}Fetching {arch} manifest...{C['RST']}")
        manifest = _get_manifest(repo, target["digest"], token)

    return manifest, token, repo


def _pick_platform(entries: list, arch: str, variant: str, image_ref: str) -> dict:
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


def _fetch_config_blob(repo: str, cfg_digest: str, token: str) -> dict:
    """Fetch the image config JSON blob; return parsed dict (empty on error)."""
    if not cfg_digest:
        return {}
    try:
        url = f"{_REGISTRY_URL}/v2/{repo}/blobs/{cfg_digest}"
        req = urllib.request.Request(url, headers={
            **_ua(),
            "Authorization": f"Bearer {token}",
        })
        with _auth_stripping_opener.open(req) as resp:
            return json.loads(resp.read())
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Layer download and application
# ---------------------------------------------------------------------------

def _fmt_size(n: int) -> str:
    if n >= 1 << 20:
        return f"{n / (1 << 20):.1f} MiB"
    if n >= 1 << 10:
        return f"{n / (1 << 10):.1f} KiB"
    return f"{n} B"


def _download_blob(repo: str, digest: str, token: str) -> str:
    """Download a blob to the layer cache; return the local file path.

    Layers are cached by digest so subsequent installs of the same image
    skip already-downloaded layers.
    """
    dest = _layer_cache_path(digest)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    if os.path.isfile(dest):
        return dest

    url = f"{_REGISTRY_URL}/v2/{repo}/blobs/{digest}"
    # Docker Hub redirects blob downloads to CDN pre-signed URLs.  The CDN
    # hosts return 400 if they receive an Authorization header, so we use a
    # custom opener that strips that header on cross-host redirects.
    req = urllib.request.Request(url, headers={
        **_ua(),
        "Authorization": f"Bearer {token}",
    })
    tmp = dest + ".tmp"
    use_tty = sys.stderr.isatty()
    try:
        with _auth_stripping_opener.open(req) as resp, open(tmp, "wb") as fh:
            total = int(resp.headers.get("Content-Length", 0))
            downloaded = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                fh.write(chunk)
                downloaded += len(chunk)
                if use_tty:
                    pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                    if total:
                        pct = downloaded * 100 // total
                        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
                        line = (f"\r{pfx}[{bar}] {pct:3d}%"
                                f"  {_fmt_size(downloaded)} / {_fmt_size(total)}{C['RST']}")
                    else:
                        line = f"\r{pfx}{_fmt_size(downloaded)} downloaded...{C['RST']}"
                    sys.stderr.write(line)
                    sys.stderr.flush()
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
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
      .wh..wh..opq   opaque whiteout — delete all contents of the parent dir
      .wh.<name>     regular whiteout — delete sibling <name>
    Hard links are copied rather than linked to keep the rootfs self-contained.
    Block/character devices and FIFOs are silently skipped.
    """
    use_tty = sys.stderr.isatty()
    done = 0

    def _tick(total: int) -> None:
        nonlocal done
        done += 1
        if not use_tty or not total:
            return
        pct = done * 100 // total
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        sys.stderr.write(
            f"\r{pfx}[{bar}] {pct:3d}%  {done} / {total} entries{C['RST']}")
        sys.stderr.flush()

    with tarfile.open(layer_path, "r:*") as tf:
        members = tf.getmembers()
        total = len(members)
        deferred_links: list = []  # list of (dest, src_path)

        for m in members:
            name = m.name.lstrip("/").rstrip("/")
            if not name or name == ".":
                _tick(total)
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
                _tick(total)
                continue

            # Regular whiteout: delete the named sibling.
            if basename.startswith(".wh."):
                _remove_fstree(os.path.join(parent, basename[4:]))
                _tick(total)
                continue

            # Skip device files and FIFOs.
            if m.isblk() or m.ischr() or m.isfifo():
                _tick(total)
                continue

            os.makedirs(parent, exist_ok=True)

            if m.isdir():
                os.makedirs(dest, exist_ok=True)
                try:
                    os.chmod(dest, stat.S_IMODE(m.mode) | stat.S_IRWXU)
                except OSError:
                    pass

            elif m.issym():
                if os.path.lexists(dest):
                    os.remove(dest)
                os.symlink(m.linkname, dest)

            elif m.islnk():
                src = os.path.join(rootfs_dir, m.linkname.lstrip("/"))
                deferred_links.append((dest, src))
                continue  # counted in the deferred pass below

            elif m.isreg():
                fobj = tf.extractfile(m)
                if fobj is None:
                    _tick(total)
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
                finally:
                    fobj.close()

            _tick(total)

        # All regular files are written; now copy hard links.
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
            _tick(total)

    if use_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def pull_image(image_ref: str, rootfs_dir: str, arch: str) -> dict:
    """Pull a Docker Hub image and extract all layers into rootfs_dir.

    Manifest and image config are cached to disk after a successful online pull.
    On subsequent calls, if the network is unavailable but the manifest and all
    layers are present in the cache, the install proceeds entirely offline.

    Returns a metadata dict with 'name', 'version', 'description' keys.
    """
    manifest = None
    token = None
    repo = None
    image_config: dict = {}
    offline = False

    # --- Attempt to resolve manifest from the registry ---
    try:
        manifest, token, repo = _resolve_single_manifest(image_ref, arch)
    except (urllib.error.URLError, OSError) as net_err:
        # Network unavailable — try the local manifest cache.
        manifest, repo, image_config = _load_manifest_cache(image_ref, arch)
        if manifest is None:
            raise RuntimeError(
                f"Network error: {net_err}\n"
                f"No cached manifest found for '{image_ref}' ({arch}). "
                f"Connect to the internet and retry."
            ) from net_err

        layers = manifest.get("layers", [])
        if not _all_layers_cached(layers):
            missing = sum(
                1 for l in layers
                if not os.path.isfile(_layer_cache_path(l["digest"]))
            )
            raise RuntimeError(
                f"Network error: {net_err}\n"
                f"{missing} of {len(layers)} layer(s) for '{image_ref}' ({arch}) "
                f"are not in the local cache. Connect to the internet and retry."
            ) from net_err

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
            f"{C['CYAN']}Network unavailable — using fully cached image "
            f"for '{image_ref}' ({arch}).{C['RST']}")
        offline = True

    layers = manifest.get("layers", [])
    if not layers:
        raise RuntimeError(
            f"Manifest for '{image_ref}' contains no filesystem layers.")

    # --- Online path: fetch image config and persist manifest to cache ---
    if not offline:
        cfg_digest = manifest.get("config", {}).get("digest", "")
        image_config = _fetch_config_blob(repo, cfg_digest, token)
        _save_manifest_cache(image_ref, arch, manifest, repo, image_config)

    # --- Download (if needed) and apply each layer ---
    n_layers = len(layers)
    for i, layer in enumerate(layers):
        digest = layer["digest"]
        media_type = layer.get("mediaType", "")
        if "zstd" in media_type:
            raise RuntimeError(
                f"Layer {i + 1}/{n_layers} uses zstd compression which is not "
                "supported by Python's tarfile module. "
                "Try a different image tag that ships gzip-compressed layers.")

        cached_path = _layer_cache_path(digest)
        if os.path.isfile(cached_path):
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Layer {i + 1}/{n_layers} already cached, skipping download.{C['RST']}")
            layer_path = cached_path
        else:
            size = layer.get("size", 0)
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Downloading layer {i + 1}/{n_layers}"
                f"{' (' + _fmt_size(size) + ')' if size else ''}...{C['RST']}")
            layer_path = _download_blob(repo, digest, token)

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Applying layer {i + 1}/{n_layers}...{C['RST']}")
        _apply_layer(layer_path, rootfs_dir)

    # --- Build return metadata from image config labels ---
    _, tag = parse_image_ref(image_ref)
    image_name = repo.split("/")[-1].capitalize()
    labels: dict = (image_config.get("config") or {}).get("Labels") or {}
    version = str(
        labels.get("org.opencontainers.image.version")
        or labels.get("version")
        or (tag if tag != "latest" else "")
    )
    description = str(
        labels.get("org.opencontainers.image.description")
        or f"Installed from Docker Hub: {image_ref}"
    )
    return {"name": image_name, "version": version, "description": description}
