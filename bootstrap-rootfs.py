#!/usr/bin/env python3
"""
Build Linux rootfs from a YAML recipe and package as tar archive.

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

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import urllib.request
import urllib.error

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install it with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# Placeholder expansion
# ---------------------------------------------------------------------------

_PLACEHOLDER_RE = re.compile(r'\$\{([^}]+)\}')


def _expand_var(expr: str, version: str, architecture: str) -> str:
    """Expand a single ${...} expression."""
    if expr == 'architecture':
        return architecture

    if expr == 'version':
        return version

    if expr.startswith('version:'):
        # Bash-style substring: ${version:offset} or ${version:offset:length}
        parts = expr.split(':')
        try:
            offset = int(parts[1])
            if len(parts) >= 3:
                length = int(parts[2])
                return version[offset:offset + length]
            return version[offset:]
        except (IndexError, ValueError) as exc:
            print(f"Warning: invalid version slice expression '${{{expr}}}': {exc}", file=sys.stderr)
            return f'${{{expr}}}'

    print(f"Warning: unknown placeholder '${{{expr}}}' — left unexpanded", file=sys.stderr)
    return f'${{{expr}}}'


def expand_placeholders(text: str, version: str, architecture: str) -> str:
    """Replace all ${...} placeholders in *text*."""
    return _PLACEHOLDER_RE.sub(
        lambda m: _expand_var(m.group(1), version, architecture),
        text,
    )


# ---------------------------------------------------------------------------
# Chroot helpers
# ---------------------------------------------------------------------------

def run_chroot_script(rootfs_dir: str, script: str, arch: str) -> None:
    """Execute *script* inside a chroot at *rootfs_dir*.

    All bind mounts and the chroot itself run within a single unshare -mpf
    session so they share one mount namespace.  Mounts are cleaned up
    automatically when the namespace exits.
    """
    setup_script_host = os.path.join(rootfs_dir, 'tmp', '_rootfs_setup.sh')
    os.makedirs(os.path.join(rootfs_dir, 'tmp'), exist_ok=True)
    with open(setup_script_host, 'w') as fh:
        fh.write('#!/bin/sh\nset -e\n')
        fh.write(script)
    os.chmod(setup_script_host, 0o755)

    # Wrapper that runs inside the unshare namespace: mount, then chroot.
    wrapper = (
        f'export DISTRIBUTION_ARCH="{arch}"\n'
        f'mount --bind "{rootfs_dir}" "{rootfs_dir}"\n'
        f'mount -t proc proc "{rootfs_dir}/proc"\n'
        f'mount --bind /sys "{rootfs_dir}/sys"\n'
        f'mount --bind /dev "{rootfs_dir}/dev"\n'
        f'chroot "{rootfs_dir}" /bin/sh /tmp/_rootfs_setup.sh\n'
    )

    try:
        for rel_dir in ('proc', 'sys', 'dev'):
            os.makedirs(os.path.join(rootfs_dir, rel_dir), exist_ok=True)

        subprocess.run(
            ['unshare', '-mpf', '/bin/sh', '-c', wrapper],
            check=True,
        )
    finally:
        try:
            os.remove(setup_script_host)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# Download helper
# ---------------------------------------------------------------------------

def download_file(url: str, dest: str) -> None:
    """Download *url* to *dest*, printing a simple progress indicator."""
    def _progress(block_count: int, block_size: int, total_size: int) -> None:
        if total_size <= 0:
            print(f"\r  Downloaded {block_count * block_size // 1024} KiB ...", end='', flush=True)
        else:
            downloaded = min(block_count * block_size, total_size)
            pct = downloaded * 100 // total_size
            print(f"\r  {pct:3d}%  {downloaded // 1024} / {total_size // 1024} KiB", end='', flush=True)

    try:
        urllib.request.urlretrieve(url, dest, reporthook=_progress)
        print()  # newline after progress
    except urllib.error.URLError as exc:
        print(file=sys.stderr)
        raise RuntimeError(f"Failed to download '{url}': {exc}") from exc


# ---------------------------------------------------------------------------
# Core build logic
# ---------------------------------------------------------------------------

def _bootstrap_rootfs(rootfs_dir: str, url: str) -> None:
    """Download and extract a pre-built rootfs archive."""
    tmpdir = os.path.dirname(rootfs_dir)
    archive = os.path.join(tmpdir, 'bootstrap.tar.gz')

    print(f"\n[1/4] Downloading bootstrap archive...")
    download_file(url, archive)

    print(f"\n[2/4] Extracting bootstrap archive...")
    subprocess.run(
        ['tar', '--numeric-owner', '--acls', '--xattrs', '--xattrs-include=*',
         '-xpf', archive, '-C', rootfs_dir],
        check=True,
    )


def _bootstrap_mmdebstrap(
    rootfs_dir: str,
    arch: str,
    version: str,
    variant: str,
    components: str,
    include_pkgs: str,
) -> None:
    """Bootstrap a rootfs using mmdebstrap."""
    print(f"\n[1/4] Bootstrapping with mmdebstrap (arch={arch}, variant={variant})...")
    cmd = [
        'mmdebstrap',
        f'--architectures={arch}',
        f'--variant={variant}',
        f'--components={components}',
        '--format=directory',
    ]
    if include_pkgs:
        cmd.append(f'--include={include_pkgs}')
    cmd += [version, rootfs_dir]

    subprocess.run(cmd, check=True)
    print(f"\n[2/4] mmdebstrap finished.")


# Mapping from distro YAML architecture names to OCI/Go architecture names.
_OCI_ARCH_MAP: dict[str, str] = {
    'aarch64':  'arm64',
    'armv7':    'arm',
    'armv7l':   'arm',
    'armhf':    'arm',
    'x86_64':   'amd64',
    'x86':      '386',
    'i386':     '386',
    'i686':     '386',
    'riscv64':  'riscv64',
    's390x':    's390x',
    'ppc64le':  'ppc64le',
    'ppc64el':  'ppc64le',
    'mips64el': 'mips64le',
}


# Mapping from raw (YAML/build) architecture names to canonical deployed names
# used in output filenames.  Raw architecture names are kept as-is throughout
# the build (URLs, mmdebstrap, OCI manifest selection, DISTRIBUTION_ARCH env).
_DEPLOYED_ARCH_MAP: dict[str, str] = {
    # aarch64
    'aarch64':  'aarch64',
    'arm64':    'aarch64',
    'arm64v8':  'aarch64',
    # arm
    'arm':      'arm',
    'armel':    'arm',
    'armhf':    'arm',
    'armhfp':   'arm',
    'armv7':    'arm',
    'armv7l':   'arm',
    'armv7a':   'arm',
    'armv8l':   'arm',
    # i686
    '386':      'i686',
    'i386':     'i686',
    'i686':     'i686',
    'x86':      'i686',
    # x86_64
    'amd64':    'x86_64',
    'x86_64':   'x86_64',
    # riscv64
    'riscv64':  'riscv64',
}


def get_deployed_arch(arch: str) -> str:
    """Return the canonical deployed architecture name for output filenames."""
    deployed = _DEPLOYED_ARCH_MAP.get(arch)
    if deployed is None:
        raise ValueError(
            f"No deployed architecture mapping for '{arch}'. "
            f"Add it to _DEPLOYED_ARCH_MAP."
        )
    return deployed


def _bootstrap_oci_container(rootfs_dir: str, arch: str, url: str) -> None:
    """Bootstrap a rootfs from an OCI Image Layout Tarball (.oci.tar)."""
    tmpdir = os.path.dirname(rootfs_dir)
    oci_tar = os.path.join(tmpdir, 'bootstrap.oci.tar')
    oci_dir = os.path.join(tmpdir, 'oci')

    print(f"\n[1/4] Downloading OCI image archive...")
    download_file(url, oci_tar)

    print(f"\n[2/4] Extracting OCI image layout and applying layers...")
    os.makedirs(oci_dir, exist_ok=True)
    subprocess.run(['tar', '-xf', oci_tar, '-C', oci_dir], check=True)

    with open(os.path.join(oci_dir, 'index.json')) as fh:
        index = json.load(fh)

    manifests = index.get('manifests', [])
    if not manifests:
        raise RuntimeError("No manifests found in OCI index.json")

    # Prefer a manifest whose platform matches the current architecture.
    oci_arch = _OCI_ARCH_MAP.get(arch, arch)
    manifest_descriptor = next(
        (m for m in manifests
         if m.get('platform', {}).get('architecture') == oci_arch
         and m.get('platform', {}).get('os', 'linux') == 'linux'),
        manifests[0],  # fall back to the first entry
    )

    algo, hex_digest = manifest_descriptor['digest'].split(':', 1)
    manifest_path = os.path.join(oci_dir, 'blobs', algo, hex_digest)
    with open(manifest_path) as fh:
        manifest = json.load(fh)

    layers = manifest.get('layers', [])
    if not layers:
        raise RuntimeError("OCI manifest contains no layers")

    for i, layer in enumerate(layers, 1):
        algo, hex_digest = layer['digest'].split(':', 1)
        layer_path = os.path.join(oci_dir, 'blobs', algo, hex_digest)
        print(f"  Applying layer {i}/{len(layers)}: {layer['digest'][:19]}...")
        subprocess.run(
            ['tar', '--numeric-owner', '--acls', '--xattrs', '--xattrs-include=*',
             '-xpf', layer_path, '-C', rootfs_dir],
            check=True,
        )


def _parse_architectures(raw: list) -> list[tuple[str, str]]:
    """Parse the architectures list from YAML into (arch, url_template) pairs.

    Each entry is either a plain string (no URL) or a single-key dict
    mapping an architecture name to a URL template.
    """
    result = []
    for entry in raw:
        if isinstance(entry, str):
            result.append((entry, ''))
        elif isinstance(entry, dict):
            if len(entry) != 1:
                raise ValueError(f"Architecture entry must have exactly one key, got: {entry}")
            arch, url = next(iter(entry.items()))
            result.append((str(arch), str(url)))
        else:
            raise ValueError(f"Unexpected architecture entry type {type(entry).__name__}: {entry}")
    return result


def build_for_arch(
    arch: str,
    version: str,
    bootstrap_method: str,
    chroot_script: str,
    name_slug: str,
    output_dir: str,
    bootstrap_url: str = '',
    mmdebstrap_variant: str = 'minbase',
    mmdebstrap_components: str = 'main',
    mmdebstrap_include_pkgs: str = '',
) -> str:
    """Build a rootfs archive for a single architecture. Returns the output path."""
    deployed_arch = get_deployed_arch(arch)
    output_name = f"{name_slug}_{version}_{deployed_arch}_rootfs.tar.xz"
    output_path = os.path.join(output_dir, output_name)

    print(f"\n{'='*60}")
    print(f"  Architecture     : {arch} → {deployed_arch}")
    print(f"  Bootstrap method : {bootstrap_method}")
    if bootstrap_method in ('rootfs', 'oci_container'):
        url = expand_placeholders(bootstrap_url, version, arch)
        print(f"  Bootstrap URL    : {url}")
    print(f"  Output           : {output_path}")
    print(f"{'='*60}")

    tmpdir = os.path.join('/tmp', 'distro_build', f'{name_slug}-{arch}-{version}')
    os.makedirs(tmpdir, exist_ok=True)
    try:
        rootfs_dir = os.path.join(tmpdir, 'rootfs')
        os.makedirs(rootfs_dir, exist_ok=True)

        # 1-2. Bootstrap.
        if bootstrap_method == 'rootfs':
            _bootstrap_rootfs(rootfs_dir, url)
        elif bootstrap_method == 'mmdebstrap':
            _bootstrap_mmdebstrap(
                rootfs_dir, arch, version,
                mmdebstrap_variant, mmdebstrap_components, mmdebstrap_include_pkgs,
            )
        elif bootstrap_method == 'oci_container':
            _bootstrap_oci_container(rootfs_dir, arch, url)
        else:
            raise ValueError(f"Unknown bootstrap_method '{bootstrap_method}'")

        # 3. Set up resolv.conf for DNS inside the chroot.
        print(f"\n[3/4] Setting up resolv.conf...")
        resolv_conf = os.path.join(rootfs_dir, 'etc', 'resolv.conf')
        os.makedirs(os.path.dirname(resolv_conf), exist_ok=True)
        # Remove regardless of whether it is a symlink or a regular file.
        if os.path.lexists(resolv_conf):
            os.remove(resolv_conf)
        with open(resolv_conf, 'w') as fh:
            fh.write('nameserver 1.1.1.1\n')

        # 4. Run chroot setup script (if provided).
        if chroot_script and chroot_script.strip():
            print(f"\n[4/4] Running setup script inside chroot...")
            run_chroot_script(rootfs_dir, chroot_script, arch)
        else:
            print(f"\n[4/4] No chroot script defined, skipping.")

        # 5. Repack.
        print(f"\nRepacking rootfs → {output_name} ...")
        subprocess.run(
            ['tar', '--sort=name', '--hard-dereference', '--numeric-owner',
             '--preserve-permissions', '--acls', '--xattrs', '--xattrs-include=*',
             '-cJf', output_path, '-C', rootfs_dir, '.'],
            check=True,
        )
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)

    print(f"\nDone: {output_path}")
    return output_path


# ---------------------------------------------------------------------------
# proot-distro config generation
# ---------------------------------------------------------------------------

def sha256_file(path: str) -> str:
    """Return the hex SHA-256 digest of the file at *path*."""
    h = hashlib.sha256()
    with open(path, 'rb') as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()


def generate_pd_config(
    output_path: str,
    name: str,
    description: str,
    version: str,
    built_archs: list[tuple[str, str, str]],  # (deployed_arch, archive_url, sha256)
    post_install_automation: str,
) -> None:
    """Write a proot-distro distribution YAML config to *output_path*."""
    lines = [
        f'name: "{name}"',
        f'description: "{description}"',
        f'version: "{version}"',
        '',
        'architectures:',
    ]
    for deployed_arch, url, checksum in built_archs:
        lines.append(f'  - {deployed_arch}: "{url}"')
        lines.append(f'    checksum: "{checksum}"')

    if post_install_automation and post_install_automation.strip():
        lines.append('')
        lines.append('post_install_automation: |')
        for line in post_install_automation.splitlines():
            lines.append(f'  {line}')

    lines.append('')
    with open(output_path, 'w') as fh:
        fh.write('\n'.join(lines))


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description='Build Linux rootfs archives from a YAML configuration file.',
    )
    parser.add_argument('yaml_file', help='Path to the YAML configuration file')
    parser.add_argument(
        '-o', '--output-dir',
        default='.',
        help='Directory where output archives are written (default: current directory)',
    )
    parser.add_argument(
        '--arch',
        metavar='ARCH',
        action='append',
        dest='selected_archs',
        help='Only build for this architecture (can be specified multiple times; default: all)',
    )
    parser.add_argument(
        '--generate-pd-config',
        action='store_true',
        help='Generate a proot-distro distribution YAML config alongside the rootfs archives',
    )
    parser.add_argument(
        '--pd-config-base-url',
        metavar='URL',
        default='https://example.com/proot-distro',
        help='Base URL for rootfs archive links in the generated proot-distro config '
             '(default: https://example.com/proot-distro)',
    )
    args = parser.parse_args()

    if os.getuid() != 0:
        print("Error: this script must be run as root.", file=sys.stderr)
        sys.exit(1)

    yaml_path = os.path.abspath(args.yaml_file)
    if not os.path.isfile(yaml_path):
        print(f"Error: '{yaml_path}' is not a file.", file=sys.stderr)
        sys.exit(1)

    with open(yaml_path, 'r') as fh:
        config = yaml.safe_load(fh)

    # Required fields.
    missing = [k for k in ('version', 'architectures') if k not in config]
    if missing:
        print(f"Error: YAML file is missing required field(s): {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)

    name: str = config.get('name', '')
    description: str = config.get('description', '')
    version: str = str(config['version'])
    bootstrap_method: str = config.get('bootstrap_method', 'rootfs') or 'rootfs'
    chroot_script: str = config.get('chroot_script', '') or ''
    post_install_automation: str = config.get('post_install_automation', '') or ''
    name_slug: str = config.get('name_slug', 'rootfs')

    _SUPPORTED_METHODS = ('rootfs', 'mmdebstrap', 'oci_container')
    if bootstrap_method not in _SUPPORTED_METHODS:
        print(f"Error: unsupported bootstrap_method '{bootstrap_method}' (expected: {', '.join(_SUPPORTED_METHODS)})", file=sys.stderr)
        sys.exit(1)

    try:
        arch_entries: list[tuple[str, str]] = _parse_architectures(config['architectures'])
    except (ValueError, TypeError) as exc:
        print(f"Error: invalid 'architectures' field: {exc}", file=sys.stderr)
        sys.exit(1)

    # Validate that URL-requiring methods have a URL for every architecture,
    # and that mmdebstrap entries are plain (no URL needed).
    url_required = bootstrap_method in ('rootfs', 'oci_container')
    for arch, url in arch_entries:
        if url_required and not url:
            print(f"Error: architecture '{arch}' has no URL (required for bootstrap_method '{bootstrap_method}')", file=sys.stderr)
            sys.exit(1)

    mmdebstrap_variant: str = str(config.get('mmdebstrap_variant', 'minbase') or 'minbase')
    mmdebstrap_components: str = str(config.get('mmdebstrap_components', 'main') or 'main')
    mmdebstrap_include_pkgs: str = str(config.get('mmdebstrap_include_pkgs', '') or '')

    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    all_arch_names = [arch for arch, _ in arch_entries]
    if args.selected_archs:
        unknown = set(args.selected_archs) - set(all_arch_names)
        if unknown:
            print(
                f"Error: requested architecture(s) not in YAML: {', '.join(sorted(unknown))}",
                file=sys.stderr,
            )
            sys.exit(1)
        build_entries = [(arch, url) for arch, url in arch_entries if arch in args.selected_archs]
    else:
        build_entries = arch_entries

    print(f"Configuration     : {yaml_path}")
    print(f"Version           : {version}")
    print(f"Bootstrap method  : {bootstrap_method}")
    print(f"Architectures     : {', '.join(arch for arch, _ in build_entries)}")
    print(f"Output dir        : {output_dir}")

    results: list[tuple[str, str | None]] = []  # (arch, output_path_or_None)

    for arch, bootstrap_url in build_entries:
        try:
            out = build_for_arch(
                arch=arch,
                version=version,
                bootstrap_method=bootstrap_method,
                chroot_script=chroot_script,
                name_slug=name_slug,
                output_dir=output_dir,
                bootstrap_url=bootstrap_url,
                mmdebstrap_variant=mmdebstrap_variant,
                mmdebstrap_components=mmdebstrap_components,
                mmdebstrap_include_pkgs=mmdebstrap_include_pkgs,
            )
            results.append((arch, out))
        except Exception as exc:
            print(f"\nError building '{arch}': {exc}", file=sys.stderr)
            results.append((arch, None))

    # Summary.
    print(f"\n{'='*60}")
    print("Build summary:")
    all_ok = True
    for arch, out in results:
        if out:
            print(f"  [OK]  {arch} → {out}")
        else:
            print(f"  [FAIL] {arch}")
            all_ok = False

    # proot-distro config generation.
    if args.generate_pd_config:
        base_url = args.pd_config_base_url.rstrip('/')
        built_archs = []
        for arch, out in results:
            if out is None:
                continue
            deployed_arch = get_deployed_arch(arch)
            archive_filename = os.path.basename(out)
            checksum = sha256_file(out)
            built_archs.append((deployed_arch, f"{base_url}/{archive_filename}", checksum))

        if built_archs:
            pd_config_path = os.path.join(output_dir, f"{name_slug}.yaml")
            generate_pd_config(
                output_path=pd_config_path,
                name=name,
                description=description,
                version=version,
                built_archs=built_archs,
                post_install_automation=post_install_automation,
            )
            print(f"\nproot-distro config written to: {pd_config_path}")
        else:
            print("\nNo successful builds; skipping proot-distro config generation.", file=sys.stderr)

    sys.exit(0 if all_ok else 1)


if __name__ == '__main__':
    main()
