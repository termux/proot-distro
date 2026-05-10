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
# proot container, or extracting a local rootfs archive. The container is
# stored at containers/<name>/rootfs with containers/<name>/manifest.json
# recording image_ref and arch (Docker mode only). Local-file mode detects
# the rootfs start depth heuristically and skips manifest/cache. All network
# and filesystem work is delegated to helpers; this module owns argument
# validation and the top-level install flow.

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
    PROGRAM_NAME,
)
from proot_distro.colors import C, msg
from proot_distro.arch import get_device_cpu_arch
from proot_distro.sysdata import setup_fake_sysdata
from proot_distro.helpers.docker import pull_image, derive_alias
from proot_distro.helpers.rootfs import (
    write_resolv_conf,
    write_hosts,
    register_android_ids,
)

_ALIAS_RE = re.compile(r'^[a-z0-9][a-z0-9_.+\-]*$')

# Top-level directory names that indicate a rootfs filesystem root.
_ROOTFS_DIRS = frozenset({
    'bin', 'dev', 'etc', 'home', 'lib', 'lib32', 'lib64', 'libx32',
    'media', 'mnt', 'opt', 'proc', 'root', 'run', 'sbin', 'srv',
    'sys', 'tmp', 'usr', 'var',
})

# Archive extensions stripped when deriving a container name from a filename.
_ARCHIVE_EXTS = (
    '.tar.gz', '.tgz', '.tar.bz2', '.tbz2', '.tar.xz', '.txz',
    '.tar.lzma', '.tlzma', '.tar',
)


def _validate_alias(alias: str) -> bool:
    return bool(_ALIAS_RE.match(alias))


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
    base = re.sub(r'[^a-z0-9_.+\-]', '-', base.lower())
    base = re.sub(r'^[^a-z0-9]+', '', base)
    base = re.sub(r'-{2,}', '-', base).strip('-')
    return base


def _detect_strip_count(members: list) -> int:
    """Return how many leading path components to strip so the first remaining
    component lands at the rootfs root (e.g. 'etc', 'usr', 'bin', ...).

    Tries strip counts 0–4, scores each by how many of the first 500 members
    have a known rootfs dir name at that depth, and picks the highest scorer.
    """
    sample = members[:500]
    best_strip, best_score = 0, -1
    for strip in range(5):
        score = 0
        for m in sample:
            parts = m.name.lstrip('/').rstrip('/').split('/')
            if len(parts) > strip and parts[strip] in _ROOTFS_DIRS:
                score += 1
        if score > best_score:
            best_score, best_strip = score, strip
    return best_strip


def _install_from_local(archive_path: str, rootfs_dir: str) -> None:
    """Extract a local rootfs archive into rootfs_dir with a progress bar."""
    use_tty = sys.stderr.isatty()

    with tarfile.open(archive_path, 'r:*') as tf:
        if use_tty:
            sys.stderr.write(
                f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                f"{C['CYAN']}Counting archive entries...{C['RST']}"
            )
            sys.stderr.flush()
        all_members = [
            m for m in tf.getmembers()
            if not (m.isblk() or m.ischr() or m.isfifo())
        ]
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

        strip = _detect_strip_count(all_members)
        total = len(all_members)
        done = 0

        def _on_entry() -> None:
            nonlocal done
            done += 1
            if not use_tty:
                return
            pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            pct = done * 100 // total if total else 100
            bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
            sys.stderr.write(
                f"\r{pfx}[{bar}] {pct:3d}%  {done} / {total} files\033[K{C['RST']}"
            )
            sys.stderr.flush()

        for member in all_members:
            parts = member.name.lstrip('/').rstrip('/').split('/')
            if len(parts) <= strip:
                _on_entry()
                continue
            rel_path = '/'.join(parts[strip:])
            if not rel_path or rel_path == '.':
                _on_entry()
                continue

            dest = os.path.join(rootfs_dir, rel_path)

            try:
                if os.path.islink(dest) or os.path.isfile(dest):
                    os.remove(dest)
                elif os.path.isdir(dest) and not member.isdir():
                    shutil.rmtree(dest)
            except OSError:
                pass

            if member.isdir():
                os.makedirs(dest, exist_ok=True)
                try:
                    os.chmod(dest, stat.S_IMODE(member.mode))
                except OSError:
                    pass

            elif member.issym():
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                try:
                    os.symlink(member.linkname, dest)
                except OSError:
                    pass

            elif member.islnk():
                lparts = member.linkname.lstrip('/').rstrip('/').split('/')
                link_src = (
                    os.path.join(rootfs_dir, '/'.join(lparts[strip:]))
                    if len(lparts) > strip else None
                )
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                if link_src:
                    try:
                        os.link(link_src, dest)
                    except OSError:
                        pass

            elif member.isreg():
                fobj = tf.extractfile(member)
                if fobj is None:
                    _on_entry()
                    continue
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                try:
                    with open(dest, 'wb') as out:
                        while True:
                            chunk = fobj.read(1 << 17)
                            if not chunk:
                                break
                            out.write(chunk)
                    try:
                        os.chmod(dest, stat.S_IMODE(member.mode))
                    except OSError:
                        pass
                finally:
                    fobj.close()

            else:
                continue

            _on_entry()

        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()


def command_install(args, configs: dict) -> None:  # noqa: ARG001
    image_ref = args.alias
    custom_dist_name = getattr(args, "custom_dist_name", None)

    if custom_dist_name is not None and not custom_dist_name:
        msg()
        msg(f"{C['BRED']}Error: container name can't be empty.{C['RST']}")
        msg()
        sys.exit(1)

    if custom_dist_name and not _validate_alias(custom_dist_name):
        msg()
        msg(f"{C['BRED']}Error: invalid container name "
            f"'{C['YELLOW']}{custom_dist_name}{C['BRED']}'. "
            f"Must start with alphanumeric and contain only "
            f"[a-z0-9_.+-].{C['RST']}")
        msg()
        sys.exit(1)

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
            if not install_name or not _validate_alias(install_name):
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
        device_arch = get_device_cpu_arch()
        dist_arch = getattr(args, "override_arch", None) or device_arch
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Installing "
            f"'{C['YELLOW']}{image_ref}{C['CYAN']}' as "
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
            _install_from_local(local_path, rootfs_dir)
        else:
            os.makedirs(DOWNLOAD_CACHE_DIR, exist_ok=True)
            metadata = pull_image(image_ref, rootfs_dir, dist_arch)

        if not os.path.isdir(os.path.join(rootfs_dir, "etc")):
            msg()
            msg(f"{C['BRED']}Error: extracted rootfs has no /etc directory. "
                f"The image may be incompatible with proot.{C['RST']}")
            msg()
            _cleanup()
            sys.exit(1)

        if local_path is None:
            manifest_data = {
                "image_ref": image_ref,
                "arch": dist_arch,
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

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Creating file '{rootfs_dir}/etc/resolv.conf'...{C['RST']}")
        write_resolv_conf(rootfs_dir)

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Creating file '{rootfs_dir}/etc/hosts'...{C['RST']}")
        write_hosts(rootfs_dir)

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Registering Android-specific UIDs and GIDs...{C['RST']}")
        register_android_ids(rootfs_dir)

        setup_fake_sysdata(rootfs_dir)

        if local_path is None:
            image_env = metadata.get("env", [])
            if image_env:
                meta_dir = os.path.join(rootfs_dir, ".proot-distro")
                os.makedirs(meta_dir, exist_ok=True)
                with open(os.path.join(meta_dir, "image-env"), "w") as fh:
                    fh.write("\n".join(image_env) + "\n")

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
    msg(f"{C['CYAN']}Log in with: "
        f"{C['GREEN']}{PROGRAM_NAME} login {install_name}{C['CYAN']}{C['RST']}")
    msg()
