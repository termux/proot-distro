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

# Architecture: Handles pulling a Docker/OCI image and setting up a new
# proot container, or extracting a local rootfs archive (plain tarball or OCI
# image layout). The container is stored at containers/<name>/rootfs with
# containers/<name>/manifest.json recording image metadata (skipped for plain
# tarballs). Local OCI images cache their layer blobs in LAYER_CACHE_DIR using
# the same path convention as the Docker pull path. All network and filesystem
# work is delegated to helpers; this module owns argument validation and the
# top-level install flow.

import json
import os
import re
import shutil
import stat
import sys
import tarfile

from proot_distro.constants import (
    CONTAINERS_DIR,
    DOWNLOAD_CACHE_DIR,
    LAYER_CACHE_DIR,
    PROGRAM_NAME,
)
from proot_distro.colors import C, msg
from proot_distro.arch import get_device_cpu_arch
from proot_distro.sysdata import setup_fake_sysdata
from proot_distro.helpers.docker import (
    pull_image,
    derive_alias,
    _apply_layer,
    _layer_cache_path,
    _ARCH_TO_DOCKER,
)
from proot_distro.helpers.rootfs import (
    write_resolv_conf,
    write_hosts,
    register_android_ids,
)
from proot_distro.helpers.download import fmt_size

_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-]*$')

# Top-level directory names that indicate a rootfs filesystem root.
_ROOTFS_DIRS = frozenset({
    'bin', 'dev', 'etc', 'home', 'lib', 'lib32', 'lib64', 'libx32',
    'media', 'mnt', 'opt', 'proc', 'root', 'run', 'sbin', 'srv',
    'sys', 'tmp', 'usr', 'var',
})

# Archive extensions stripped when deriving a container name from a filename.
_ARCHIVE_EXTS = (
    '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz',
    '.oci.tar.xz', '.oci.tar.gz', '.oci.tar',
    '.tar.lzma', '.tlzma', '.tar',
)

# Reverse of _ARCH_TO_DOCKER: Docker architecture name → proot-distro arch.
_DOCKER_TO_ARCH = {docker: pd for pd, (docker, _) in _ARCH_TO_DOCKER.items()}


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

def _validate_name(name: str) -> bool:
    return bool(_NAME_RE.match(name))


def _is_local_path(ref: str) -> bool:
    """Return True if ref should be treated as a local file path."""
    if ref.startswith(('/', './', '../', '~')):
        return True
    return os.path.isfile(os.path.expanduser(ref))


def _derive_local_name(path: str) -> str:
    """Derive a container alias from an archive filename.

    Returns an empty string if a valid name cannot be derived.
    """
    base = os.path.basename(path)
    low = base.lower()
    for ext in _ARCHIVE_EXTS:
        if low.endswith(ext):
            base = base[:-len(ext)]
            break
    base = re.sub(r'[^a-z0-9_.\-]', '-', base.lower())
    base = re.sub(r'^[^a-z0-9]+', '', base)
    base = re.sub(r'-{2,}', '-', base).strip('-')
    return base


# ---------------------------------------------------------------------------
# Plain tarball extraction
# ---------------------------------------------------------------------------

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


def _detect_strip_count(member_names: list) -> int:
    """Return how many leading path components to strip so the first remaining
    component lands at the rootfs root (e.g. 'etc', 'usr', 'bin', ...).

    Tries strip counts 0–4, scores each by how many of the first 500 names
    have a known rootfs dir name at that depth, and picks the highest scorer.
    """
    sample = member_names[:500]
    best_strip, best_score = 0, -1
    for strip in range(5):
        score = 0
        for name in sample:
            parts = name.lstrip('/').rstrip('/').split('/')
            if len(parts) > strip and parts[strip] in _ROOTFS_DIRS:
                score += 1
        if score > best_score:
            best_score, best_strip = score, strip
    return best_strip


def _extract_plain_tar(
    archive_path: str, strip: int, total_size: int, rootfs_dir: str
) -> None:
    """Stream-extract a plain rootfs tarball into rootfs_dir.

    - Block/character devices and FIFOs are silently skipped.
    - Hard links are copied via shutil.copy2 after all regular files are
      written, so the link source is guaranteed to exist.
    - mtimes are preserved on regular files and symlinks.
    - Directory mtimes are applied last (writing into a dir updates its mtime).
    - Directories get at least S_IRWXU so subsequent writes into them succeed.
    - Progress is tracked via _ByteCounter (compressed bytes consumed) so the
      bar advances smoothly without an upfront archive scan.
    """
    use_tty = sys.stderr.isatty()
    _last_shown = 0

    def _show(counter: _ByteCounter) -> None:
        nonlocal _last_shown
        if not use_tty or not total_size:
            return
        done = counter.count
        if done - _last_shown < 262144:
            return
        _last_shown = done
        pct = min(done * 100 // total_size, 100)
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        sys.stderr.write(
            f"\r{pfx}[{bar}] {pct:3d}%  "
            f"{fmt_size(done)} / {fmt_size(total_size)}\033[K{C['RST']}"
        )
        sys.stderr.flush()

    deferred_links: list = []  # (dest, src) — copied after all regular files
    deferred_dirs: list = []   # (dest, mtime) — stamped after all writes

    with open(archive_path, 'rb') as raw_fh:
        counter = _ByteCounter(raw_fh)
        with tarfile.open(fileobj=counter, mode='r|*') as tf:
            for member in tf:
                if member.isblk() or member.ischr() or member.isfifo():
                    _show(counter)
                    continue

                parts = member.name.lstrip('/').rstrip('/').split('/')
                if len(parts) <= strip:
                    _show(counter)
                    continue
                rel_parts = parts[strip:]
                rel_path = '/'.join(rel_parts)
                if not rel_path or rel_path == '.':
                    _show(counter)
                    continue

                parent = (
                    os.path.join(rootfs_dir, *rel_parts[:-1])
                    if len(rel_parts) > 1 else rootfs_dir
                )
                dest = os.path.join(rootfs_dir, rel_path)

                os.makedirs(parent, exist_ok=True)

                if member.isdir():
                    os.makedirs(dest, exist_ok=True)
                    try:
                        os.chmod(dest, stat.S_IMODE(member.mode) | stat.S_IRWXU)
                    except OSError:
                        pass
                    deferred_dirs.append((dest, member.mtime))

                elif member.issym():
                    if os.path.lexists(dest):
                        os.remove(dest)
                    try:
                        os.symlink(member.linkname, dest)
                        try:
                            os.utime(dest, (member.mtime, member.mtime),
                                     follow_symlinks=False)
                        except OSError:
                            pass
                    except OSError:
                        pass

                elif member.islnk():
                    lparts = member.linkname.lstrip('/').rstrip('/').split('/')
                    if len(lparts) > strip:
                        link_src = os.path.join(
                            rootfs_dir, '/'.join(lparts[strip:])
                        )
                        deferred_links.append((dest, link_src))
                    _show(counter)
                    continue

                elif member.isreg():
                    fobj = tf.extractfile(member)
                    if fobj is None:
                        _show(counter)
                        continue
                    if os.path.lexists(dest):
                        try:
                            os.remove(dest)
                        except OSError:
                            pass
                    try:
                        with open(dest, 'wb') as out:
                            while True:
                                chunk = fobj.read(1 << 17)  # 128 KiB
                                if not chunk:
                                    break
                                out.write(chunk)
                                _show(counter)
                        try:
                            os.chmod(dest, stat.S_IMODE(member.mode))
                        except OSError:
                            pass
                        try:
                            os.utime(dest, (member.mtime, member.mtime))
                        except OSError:
                            pass
                    finally:
                        fobj.close()

                else:
                    _show(counter)
                    continue

                _show(counter)

    # All regular files written — now copy hard links (shutil.copy2 preserves mtime).
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

    # Apply directory timestamps last.
    for path, mtime in reversed(deferred_dirs):
        try:
            os.utime(path, (mtime, mtime))
        except OSError:
            pass

    if use_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()


# ---------------------------------------------------------------------------
# OCI image layout extraction
# ---------------------------------------------------------------------------

def _oci_blob_path(digest: str) -> str:
    """Convert 'sha256:abc123' to 'blobs/sha256/abc123'."""
    algo, hex_val = digest.split(':', 1)
    return f"blobs/{algo}/{hex_val}"


def _oci_read_json(tf: tarfile.TarFile, member_map: dict, path: str) -> dict:
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


def _oci_find_manifest_entry(
    tf: tarfile.TarFile,
    member_map: dict,
    index_manifests: list,
    dist_arch: str,
) -> dict:
    """Pick the index manifest entry that matches dist_arch.

    Strategy:
    1. Single entry — use it regardless of arch (trust the caller knows).
    2. Multiple entries with platform.architecture — filter by docker arch.
    3. Multiple entries without platform — read each config blob to check.

    Raises RuntimeError when no matching entry is found.
    """
    if len(index_manifests) == 1:
        return index_manifests[0]

    docker_arch = _ARCH_TO_DOCKER.get(dist_arch, (dist_arch, ''))[0]

    # Try fast path: platform field present in index entries.
    platform_entries = [
        e for e in index_manifests if 'platform' in e
    ]
    if platform_entries:
        for entry in platform_entries:
            p = entry['platform']
            if p.get('architecture') == docker_arch and p.get('os') == 'linux':
                return entry
        raise RuntimeError(
            f"No manifest found for architecture '{dist_arch}' "
            f"in OCI index (tried {docker_arch})."
        )

    # Slow path: read each manifest → config to detect architecture.
    for entry in index_manifests:
        manifest = _oci_read_json(
            tf, member_map, _oci_blob_path(entry['digest'])
        )
        config_digest = manifest.get('config', {}).get('digest', '')
        if not config_digest:
            continue
        config = _oci_read_json(
            tf, member_map, _oci_blob_path(config_digest)
        )
        if config.get('architecture') == docker_arch:
            return entry

    raise RuntimeError(
        f"No manifest found for architecture '{dist_arch}' "
        f"in OCI image (tried {docker_arch})."
    )


def _oci_cache_layer(
    tf: tarfile.TarFile, member_map: dict, digest: str
) -> str:
    """Extract a layer blob from the outer archive into LAYER_CACHE_DIR.

    Returns the cache path. The blob is written atomically via a .tmp file.
    """
    blob_path = _oci_blob_path(digest)
    member = member_map.get(blob_path)
    if member is None:
        raise RuntimeError(f"OCI archive is missing layer blob: {blob_path}")
    os.makedirs(LAYER_CACHE_DIR, exist_ok=True)
    cache_path = _layer_cache_path(digest)
    tmp = cache_path + '.tmp'
    fobj = tf.extractfile(member)
    if fobj is None:
        raise RuntimeError(f"OCI layer blob is not a regular file: {blob_path}")
    try:
        with open(tmp, 'wb') as out:
            shutil.copyfileobj(fobj, out)
    finally:
        fobj.close()
    os.replace(tmp, cache_path)
    return cache_path


def _extract_oci(
    tf: tarfile.TarFile,
    member_map: dict,
    rootfs_dir: str,
    dist_arch: str,
) -> dict:
    """Install from an OCI image layout (tf already open).

    Reads index.json, selects the manifest for dist_arch, caches each layer
    blob in LAYER_CACHE_DIR, and applies the layers via _apply_layer().

    Returns a metadata dict compatible with the manifest.json schema:
      manifest, image_config, image_ref, arch, env.
    """
    index = _oci_read_json(tf, member_map, 'index.json')
    index_manifests = index.get('manifests', [])
    if not index_manifests:
        raise RuntimeError("OCI index.json contains no manifests.")

    manifest_entry = _oci_find_manifest_entry(
        tf, member_map, index_manifests, dist_arch
    )

    manifest = _oci_read_json(
        tf, member_map, _oci_blob_path(manifest_entry['digest'])
    )

    config_digest = manifest.get('config', {}).get('digest', '')
    if not config_digest:
        raise RuntimeError("OCI image manifest has no config digest.")
    image_config = _oci_read_json(tf, member_map, _oci_blob_path(config_digest))

    # Determine the actual arch from the image config.
    docker_arch = image_config.get('architecture', '')
    actual_arch = _DOCKER_TO_ARCH.get(docker_arch, dist_arch)

    layers = manifest.get('layers', [])
    if not layers:
        raise RuntimeError("OCI image manifest contains no layers.")

    n_layers = len(layers)
    for i, layer in enumerate(layers):
        digest = layer['digest']
        short_id = digest[:19]
        size = layer.get('size', 0)
        size_str = f" ({fmt_size(size)})" if size else ""
        cache_path = _layer_cache_path(digest)

        if os.path.isfile(cache_path):
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"{short_id}: Layer {i + 1}/{n_layers} already cached, "
                f"skipping.{C['RST']}")
        else:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"{short_id}: Caching layer "
                f"{i + 1}/{n_layers}{size_str}...{C['RST']}")
            _oci_cache_layer(tf, member_map, digest)

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"{short_id}: Applying layer {i + 1}/{n_layers}...{C['RST']}")
        _apply_layer(cache_path, rootfs_dir)

    # Derive image_ref from index entry annotations if available.
    annotations = manifest_entry.get('annotations', {})
    image_ref = (
        annotations.get('io.containerd.image.name')
        or annotations.get('org.opencontainers.image.ref.name')
        or ''
    )

    env = (image_config.get('config') or {}).get('Env') or []

    return {
        'manifest': manifest,
        'image_config': image_config,
        'image_ref': image_ref,
        'arch': actual_arch,
        'env': env,
    }


# ---------------------------------------------------------------------------
# Local-file entry point (dispatches to plain tar or OCI)
# ---------------------------------------------------------------------------

def _install_from_local_file(
    archive_path: str, rootfs_dir: str, dist_arch: str
) -> dict | None:
    """Open archive_path, detect its format, and extract into rootfs_dir.

    Returns a metadata dict for OCI images (same schema as pull_image()), or
    None for plain tarballs (no manifest.json is written for those).

    Format detection and strip-count determination use a streaming probe that
    reads at most the first 500 member headers — no full upfront scan needed
    for plain tarballs. OCI image layouts still require a full getmembers()
    pass because layer blobs must be accessed by digest in arbitrary order.
    """
    total_size = os.path.getsize(archive_path)

    # Streaming probe: read up to 500 member names to detect OCI layout and
    # determine the strip count for plain tarballs. For compressed archives
    # this decompresses only the leading portion of the file (fast).
    probe_names: list = []
    is_oci = False
    with tarfile.open(archive_path, 'r|*') as tf_probe:
        for m in tf_probe:
            probe_names.append(m.name)
            if m.name == 'oci-layout':
                is_oci = True
                break
            if len(probe_names) >= 500:
                break

    if is_oci:
        # OCI image layout: blobs are accessed by digest in arbitrary order,
        # so random access via getmembers() is required.
        use_tty = sys.stderr.isatty()
        with tarfile.open(archive_path, 'r:*') as tf:
            if use_tty:
                sys.stderr.write(
                    f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                    f"{C['CYAN']}Indexing OCI archive...{C['RST']}"
                )
                sys.stderr.flush()
            raw_members = tf.getmembers()
            if use_tty:
                sys.stderr.write("\r\033[K")
                sys.stderr.flush()
            member_map = {m.name: m for m in raw_members}
            return _extract_oci(tf, member_map, rootfs_dir, dist_arch)

    strip = _detect_strip_count(probe_names)
    _extract_plain_tar(archive_path, strip, total_size, rootfs_dir)
    return None


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

def command_install(args, configs: dict) -> None:  # noqa: ARG001
    image_ref = args.alias
    custom_dist_name = getattr(args, "custom_dist_name", None)

    if custom_dist_name is not None and not custom_dist_name:
        msg()
        msg(f"{C['BRED']}Error: container name can't be empty.{C['RST']}")
        msg()
        sys.exit(1)

    if custom_dist_name and not _validate_name(custom_dist_name):
        msg()
        msg(f"{C['BRED']}Error: container name "
            f"'{C['YELLOW']}{custom_dist_name}{C['BRED']}' is not valid. "
            f"It must begin with a letter or digit and contain only "
            f"letters, digits, underscores, dots, or hyphens.{C['RST']}")
        msg()
        sys.exit(1)

    device_arch = get_device_cpu_arch()
    dist_arch = getattr(args, "override_arch", None) or device_arch

    # Decide between local-file mode and Docker-pull mode.
    local_path = os.path.expanduser(image_ref) if _is_local_path(image_ref) else None

    if local_path is not None:
        if not os.path.isfile(local_path):
            msg()
            msg(f"{C['BRED']}Error: local file "
                f"'{C['YELLOW']}{local_path}{C['BRED']}' does not exist "
                f"or is not a regular file.{C['RST']}")
            msg()
            sys.exit(1)
        if custom_dist_name:
            install_name = custom_dist_name
        else:
            install_name = _derive_local_name(local_path)
            if not install_name or not _validate_name(install_name):
                msg()
                msg(f"{C['BRED']}Error: cannot determine a valid container "
                    f"name from "
                    f"'{C['YELLOW']}{os.path.basename(local_path)}{C['BRED']}'. "
                    f"Use '{C['YELLOW']}--name NAME{C['BRED']}' to specify "
                    f"one.{C['RST']}")
                msg()
                sys.exit(1)
    else:
        install_name = custom_dist_name if custom_dist_name else derive_alias(image_ref)

    container_dir = os.path.join(CONTAINERS_DIR, install_name)
    rootfs_dir = os.path.join(container_dir, "rootfs")

    if os.path.isdir(rootfs_dir):
        msg()
        msg(f"{C['BRED']}Error: container "
            f"'{C['YELLOW']}{install_name}{C['BRED']}' already exists. "
            f"Use a different name with "
            f"'{C['YELLOW']}--name custom_name{C['BRED']}'.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Log in:     "
            f"{C['GREEN']}{PROGRAM_NAME} login {install_name}{C['RST']}")
        msg(f"{C['CYAN']}Reinstall:  "
            f"{C['GREEN']}{PROGRAM_NAME} reset {install_name}{C['RST']}")
        msg(f"{C['CYAN']}Uninstall:  "
            f"{C['GREEN']}{PROGRAM_NAME} remove {install_name}{C['RST']}")
        msg()
        sys.exit(1)

    if local_path is not None:
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Installing "
            f"from '{C['YELLOW']}{os.path.basename(local_path)}{C['CYAN']}' "
            f"as '{C['YELLOW']}{install_name}{C['CYAN']}'...{C['RST']}")
    else:
        # Always show the tag, appending ':latest' when the user omitted it.
        last_component = image_ref.split("/")[-1]
        display_ref = (
            image_ref if ":" in last_component else f"{image_ref}:latest"
        )
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Installing "
            f"'{C['YELLOW']}{display_ref}{C['CYAN']}' as "
            f"'{C['YELLOW']}{install_name}{C['CYAN']}'...{C['RST']}")

    os.makedirs(rootfs_dir, exist_ok=True)

    def _cleanup() -> None:
        try:
            shutil.rmtree(container_dir)
        except OSError:
            pass

    try:
        if local_path is not None:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Extracting rootfs from archive...{C['RST']}")
            metadata = _install_from_local_file(local_path, rootfs_dir, dist_arch)
        else:
            os.makedirs(DOWNLOAD_CACHE_DIR, exist_ok=True)
            metadata = pull_image(image_ref, rootfs_dir, dist_arch)

        # Write manifest.json when metadata is available (Docker pull or OCI
        # local). Skipped for plain tarballs (metadata is None).
        if metadata is not None:
            manifest_data = {
                "image_ref": (
                    metadata.get("image_ref") or
                    (image_ref if local_path is None else "")
                ),
                "arch": metadata.get("arch") or dist_arch,
                "manifest": metadata.get("manifest", {}),
                "image_config": metadata.get("image_config", {}),
            }
            manifest_path = os.path.join(container_dir, "manifest.json")
            try:
                with open(manifest_path, "w") as fh:
                    json.dump(manifest_data, fh, indent=2)
            except OSError as exc:
                msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
                    f"Warning: could not write manifest.json: {exc}{C['RST']}")

        if os.path.isdir(os.path.join(rootfs_dir, "etc")):
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Updating '/etc/resolv.conf'...{C['RST']}")
            write_resolv_conf(rootfs_dir)

            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Updating '/etc/hosts'...{C['RST']}")
            write_hosts(rootfs_dir)

            if os.path.isfile(os.path.join(rootfs_dir, "etc", "passwd")):
                msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                    f"Registering Android-specific UIDs and GIDs...{C['RST']}")
                register_android_ids(rootfs_dir)

        setup_fake_sysdata(rootfs_dir)

    except KeyboardInterrupt:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Aborted by user.{C['RST']}")
        _cleanup()
        sys.exit(1)
    except (EOFError, OSError, tarfile.TarError, RuntimeError) as exc:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Failed to install: {exc}{C['RST']}")
        _cleanup()
        sys.exit(1)
    except Exception:
        _cleanup()
        raise

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Finished installation.{C['RST']}")
    msg()
    msg(f"{C['CYAN']}Start shell:    "
        f"{C['GREEN']}{PROGRAM_NAME} login {install_name}{C['RST']}")
    entrypoint = (
        (metadata.get("image_config") or {}).get("config", {}).get("Entrypoint")
        if metadata else None
    )
    if entrypoint:
        msg(f"{C['CYAN']}Run entrypoint: "
            f"{C['GREEN']}{PROGRAM_NAME} run {install_name}{C['RST']}")
    msg()
