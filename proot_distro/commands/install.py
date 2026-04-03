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
import os
import re
import shutil
import stat
import sys
import tarfile
import zipfile

import yaml

from proot_distro.constants import (
    PD_CONFIGS_DIR,
    DOWNLOAD_CACHE_DIR,
    INSTALLED_ROOTFS_DIR,
    PROGRAM_NAME,
)
from proot_distro.colors import C, msg
from proot_distro.config import load_config, _write_yaml, expand_url
from proot_distro.arch import get_device_cpu_arch
from proot_distro.sysdata import setup_fake_sysdata
from proot_distro.download import sha256_file, download_file
from proot_distro.rootfs import (
    _write_environment,
    _fix_path_in_configs,
    _write_resolv_conf,
    _write_hosts,
    _register_android_ids,
    _run_post_install,
)

_ALIAS_RE = re.compile(r'^[a-z0-9][a-z0-9_.+\-]*$')


def _make_dir(dest: str, mode: int) -> None:
    os.makedirs(dest, exist_ok=True)
    try:
        # Always guarantee owner rwx so proot can traverse and write.
        os.chmod(dest, stat.S_IMODE(mode) | stat.S_IRWXU)
    except OSError:
        pass


def _make_symlink(dest: str, target: str) -> None:
    if os.path.lexists(dest):
        os.remove(dest)
    os.symlink(target, dest)


def _detect_archive_type(path: str) -> str:
    """Return 'zip' or 'tar' based on file magic bytes."""
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
        if magic == b'PK\x03\x04':
            return "zip"
    except OSError:
        pass
    return "tar"


def _stripped_name(name: str, strip: int):
    """Remove the first *strip* path components from a tar entry name.

    Returns the remaining path string, or None if there are not enough
    components (i.e. the entry lives entirely within the stripped prefix).
    """
    parts = [p for p in name.replace('\\', '/').lstrip('/').split('/') if p]
    if strip >= len(parts):
        return None
    return '/'.join(parts[strip:])


def _extract_tar(archive_path: str, dest_dir: str,
                 strip: int = 0, exclude_dev: bool = False) -> None:
    """Extract a tar archive (any compression) into dest_dir.

    Differences from a plain tar -x:
    - Hard links are extracted as file copies rather than actual hard links,
      keeping the rootfs self-contained and proot-friendly.
    - All directories receive at least owner rwx (0o700) so proot can always
      traverse and write into them, regardless of what the archive stores.
    - Block/character devices, FIFOs and sockets are silently skipped.
    - Symlinks are stored as-is (not followed).
    - A TTY progress bar is shown on stderr.
    """
    use_tty = sys.stderr.isatty()
    done = 0

    def _on_entry(total: int) -> None:
        nonlocal done
        done += 1
        if not use_tty:
            return
        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        pct = done * 100 // total if total else 100
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        sys.stderr.write(f"\r{pfx}[{bar}] {pct:3d}%  {done} / {total} files{C['RST']}")
        sys.stderr.flush()

    with tarfile.open(archive_path, 'r:*') as tf:
        # Pre-collect members to obtain the total for an accurate progress bar.
        if use_tty:
            sys.stderr.write(
                f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] "
                f"{C['CYAN']}Estimating progress...{C['RST']}")
            sys.stderr.flush()
        members = [m for m in tf.getmembers()
                   if not (m.isblk() or m.ischr() or m.isfifo())]
        total = len(members)
        if use_tty:
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()

        # Hard links are deferred until all regular files have been written so
        # that shutil.copy2 always finds the source file already on disk.
        deferred_hardlinks = []  # list of (dest, src_path)

        for member in members:
            name = _stripped_name(member.name, strip)
            if not name or (exclude_dev and
                            (name == 'dev' or name.startswith('dev/'))):
                _on_entry(total)
                continue

            dest = os.path.join(dest_dir, name)

            if member.isdir():
                _make_dir(dest, member.mode)

            elif member.issym():
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                _make_symlink(dest, member.linkname)

            elif member.islnk():
                src_name = _stripped_name(member.linkname, strip)
                if src_name:
                    parent = os.path.dirname(dest)
                    if parent:
                        os.makedirs(parent, exist_ok=True)
                    deferred_hardlinks.append((dest, os.path.join(dest_dir, src_name)))
                    # _on_entry is called in the deferred phase below.
                    continue
                else:
                    # Unresolvable — count it now so the total stays accurate.
                    _on_entry(total)
                    continue

            elif member.isreg():
                fobj = tf.extractfile(member)
                if fobj is None:
                    _on_entry(total)
                    continue
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                try:
                    with open(dest, 'wb') as out:
                        shutil.copyfileobj(fobj, out)
                    try:
                        os.chmod(dest, stat.S_IMODE(member.mode))
                    except OSError:
                        pass
                finally:
                    fobj.close()

            else:
                _on_entry(total)
                continue

            _on_entry(total)

        # All regular files are now fully written — safe to copy hard links.
        for dest, src_path in deferred_hardlinks:
            if os.path.isfile(src_path):
                shutil.copy2(src_path, dest)
            _on_entry(total)

    if use_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()


def _extract_zip(archive_path: str, rootfs_dir: str, strip: int, l2s_dir: str) -> None:  # noqa: ARG001
    """Extract a ZIP archive into rootfs_dir, handling symlinks and permissions."""
    use_tty = sys.stderr.isatty()
    done = 0

    def _on_entry(total: int) -> None:
        nonlocal done
        done += 1
        if not use_tty:
            return
        pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        pct = done * 100 // total if total else 100
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        sys.stderr.write(f"\r{pfx}[{bar}] {pct:3d}%  {done} / {total} files{C['RST']}")
        sys.stderr.flush()

    with zipfile.ZipFile(archive_path, "r") as zf:
        entries = zf.infolist()
        total = len(entries)

        for info in entries:
            parts = [p for p in info.filename.split("/") if p]
            if strip:
                if len(parts) <= strip:
                    _on_entry(total)
                    continue
                parts = parts[strip:]
            if not parts or parts[0] == "dev":
                _on_entry(total)
                continue

            name = "/".join(parts)
            dest = os.path.join(rootfs_dir, name)
            attr = info.external_attr >> 16

            if stat.S_ISLNK(attr):
                target = zf.read(info.filename).decode("utf-8", errors="replace")
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                _make_symlink(dest, target)

            elif info.filename.endswith("/") or stat.S_ISDIR(attr):
                _make_dir(dest, attr)

            else:
                parent = os.path.dirname(dest)
                if parent:
                    os.makedirs(parent, exist_ok=True)
                with zf.open(info) as src, open(dest, "wb") as fh:
                    shutil.copyfileobj(src, fh)
                m = stat.S_IMODE(attr)
                if m:
                    try:
                        os.chmod(dest, m)
                    except OSError:
                        pass

            _on_entry(total)

    if use_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()


def _validate_alias(alias: str) -> bool:
    return bool(_ALIAS_RE.match(alias)) and not alias.endswith(".yaml")


def _install_termux_rootfs(archive_path: str, rootfs_dir: str, strip: int) -> None:
    """Handle extraction and setup for type=termux distributions."""
    termux_usr = os.path.join(rootfs_dir, "data", "data", "com.termux", "files", "usr")
    termux_home = os.path.join(rootfs_dir, "data", "data", "com.termux", "files", "home")
    os.makedirs(termux_home, exist_ok=True)
    os.makedirs(termux_usr, exist_ok=True)

    if _detect_archive_type(archive_path) == "zip":
        _extract_zip(archive_path, termux_usr, strip, termux_usr)
    else:
        _extract_tar(archive_path, termux_usr, strip, exclude_dev=False)

    symlinks_file = os.path.join(termux_usr, "SYMLINKS.txt")
    if os.path.isfile(symlinks_file):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Creating symlinks, please wait...{C['RST']}")
        with open(symlinks_file, encoding="utf-8") as fh:
            for line in fh:
                line = line.rstrip("\n")
                if "\u2190" not in line:
                    continue
                src, dst = line.split("\u2190", 1)
                full_dst = os.path.join(termux_usr, dst.lstrip("/"))
                dst_parent = os.path.dirname(full_dst)
                if dst_parent:
                    os.makedirs(dst_parent, exist_ok=True)
                if os.path.lexists(full_dst):
                    os.remove(full_dst)
                os.symlink(src, full_dst)
        os.remove(symlinks_file)

    bashrc = os.path.join(termux_usr, "etc", "bash.bashrc")
    if os.path.isfile(bashrc):
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Updating PS1 in bash.bashrc...{C['RST']}")
        with open(bashrc, "a") as fh:
            fh.write("\n# Added by proot-distro:\n")
            fh.write(r'PS1="\\[\\e[0;35m\\]\\\PD\\\\\[\\e[0m\\] ${PS1}"' + "\n")


def command_install(args, configs: dict) -> None:
    dist_name = args.alias
    custom_dist_name = getattr(args, "custom_dist_name", None)

    if dist_name not in configs:
        msg()
        msg(f"{C['BRED']}Error: unknown distribution '{C['YELLOW']}{dist_name}{C['BRED']}' was requested to be installed.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}View supported distributions by: {C['GREEN']}{PROGRAM_NAME} list{C['RST']}")
        msg()
        sys.exit(1)

    cfg = configs[dist_name]
    install_name = dist_name

    if custom_dist_name is not None and not custom_dist_name:
        msg()
        msg(f"{C['BRED']}Error: distribution name can't be empty.{C['RST']}")
        msg()
        sys.exit(1)

    if custom_dist_name:
        if not _validate_alias(custom_dist_name):
            msg()
            msg(f"{C['BRED']}Error: invalid alias '{C['YELLOW']}{custom_dist_name}{C['BRED']}'. "
                f"Must start with alphanumeric and contain only [a-z0-9_.+-].{C['RST']}")
            msg()
            sys.exit(1)
        if custom_dist_name in configs \
                or os.path.exists(os.path.join(PD_CONFIGS_DIR, custom_dist_name + ".yaml")) \
                or os.path.isdir(os.path.join(INSTALLED_ROOTFS_DIR, custom_dist_name)):
            msg()
            msg(f"{C['BRED']}Error: distribution with alias '{C['YELLOW']}{custom_dist_name}{C['BRED']}' already exists.{C['RST']}")
            msg()
            sys.exit(1)
        custom_config_path = os.path.join(PD_CONFIGS_DIR, custom_dist_name + ".yaml")
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Creating config '{custom_config_path}'...{C['RST']}")
        with open(cfg.config_path) as fh:
            orig_data = yaml.safe_load(fh)
        _write_yaml(custom_config_path, orig_data)
        install_name = custom_dist_name
        cfg = load_config(custom_config_path, custom_dist_name)

    rootfs_dir = os.path.join(INSTALLED_ROOTFS_DIR, install_name)
    if os.path.isdir(rootfs_dir):
        msg()
        msg(f"{C['BRED']}Error: distribution '{C['YELLOW']}{install_name}{C['BRED']}' is already installed.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Log in:     {C['GREEN']}{PROGRAM_NAME} login {install_name}{C['RST']}")
        msg(f"{C['CYAN']}Reinstall:  {C['GREEN']}{PROGRAM_NAME} reset {install_name}{C['RST']}")
        msg(f"{C['CYAN']}Uninstall:  {C['GREEN']}{PROGRAM_NAME} remove {install_name}{C['RST']}")
        msg()
        sys.exit(1)

    # Determine architecture.
    device_arch = get_device_cpu_arch()
    dist_arch = getattr(args, "override_arch", None) or device_arch
    arch_entry = next((e for e in cfg.architectures if e.arch == dist_arch), None)
    if arch_entry is None:
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}The distribution is not available for CPU architecture '{dist_arch}'.{C['RST']}")
        sys.exit(1)

    override_url = getattr(args, "override_url", None)
    override_checksum = getattr(args, "override_checksum", None)

    url = override_url or expand_url(arch_entry.url, cfg.version, dist_arch)
    checksum = override_checksum if override_url else (override_checksum or arch_entry.checksum)

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Installing {C['YELLOW']}{cfg.name}{C['CYAN']}...{C['RST']}")

    os.makedirs(rootfs_dir, exist_ok=True)

    def _cleanup():
        try:
            shutil.rmtree(rootfs_dir)
        except OSError:
            pass
        if custom_dist_name:
            try:
                os.remove(os.path.join(PD_CONFIGS_DIR, custom_dist_name + ".yaml"))
            except OSError:
                pass

    try:
        os.makedirs(DOWNLOAD_CACHE_DIR, exist_ok=True)
        archive_name = os.path.basename(url.split("?")[0])
        archive_path = os.path.join(DOWNLOAD_CACHE_DIR, archive_name)

        if os.path.isfile(archive_path):
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Using cached rootfs archive...{C['RST']}")
        else:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Downloading rootfs archive...{C['RST']}")
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}URL: {url}{C['RST']}")
            download_file(url, archive_path)

        if checksum:
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Verifying rootfs checksum...{C['RST']}")
            actual = sha256_file(archive_path)
            if actual != checksum:
                msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}Verification failure: downloaded file is corrupted.{C['RST']}")
                os.remove(archive_path)
                _cleanup()
                sys.exit(1)
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Integrity check passed.{C['RST']}")
        else:
            msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}Integrity checking of downloaded rootfs has been disabled.{C['RST']}")

        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Extracting rootfs, please wait...{C['RST']}")

        override_strip = getattr(args, "override_strip", None)
        if override_strip is not None and override_strip < 0:
            msg()
            msg(f"{C['BRED']}Error: --strip-path-components must be a non-negative integer.{C['RST']}")
            msg()
            _cleanup()
            sys.exit(1)
        strip_path_components = override_strip if override_strip is not None else cfg.strip_path_components

        if cfg.dist_type == "termux":
            _install_termux_rootfs(archive_path, rootfs_dir, strip_path_components)
        else:
            if _detect_archive_type(archive_path) == "zip":
                _extract_zip(archive_path, rootfs_dir, strip_path_components, rootfs_dir)
            else:
                _extract_tar(archive_path, rootfs_dir, strip_path_components, exclude_dev=True)

            if not os.path.isdir(os.path.join(rootfs_dir, "etc")):
                msg()
                msg(f"{C['BRED']}Error: the rootfs of distribution '{C['YELLOW']}{install_name}{C['BRED']}' has unexpected structure "
                    f"(no /etc directory). Make sure that variable TARBALL_STRIP_OPT specified in distribution plug-in is correct.{C['RST']}")
                msg()
                _cleanup()
                sys.exit(1)

            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Writing file '{rootfs_dir}/etc/environment'...{C['RST']}")
            _write_environment(rootfs_dir)
            _fix_path_in_configs(rootfs_dir)

            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Creating file '{rootfs_dir}/etc/resolv.conf'...{C['RST']}")
            _write_resolv_conf(rootfs_dir)

            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Creating file '{rootfs_dir}/etc/hosts'...{C['RST']}")
            _write_hosts(rootfs_dir)

            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Registering Android-specific UIDs and GIDs...{C['RST']}")
            _register_android_ids(rootfs_dir)

            setup_fake_sysdata(install_name)

        if cfg.post_install_automation.strip():
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Running distribution-specific configuration steps...{C['RST']}")
            _run_post_install(install_name, cfg.post_install_automation, dist_arch)

    except KeyboardInterrupt:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}Aborted by user.{C['RST']}")
        _cleanup()
        sys.exit(1)
    except (EOFError, OSError, tarfile.TarError) as exc:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}Failed to install distribution: {exc}{C['RST']}")
        msg()
        msg(f"{C['BRED']}The archive may be corrupted or incompatible.{C['RST']}")
        msg()
        _cleanup()
        sys.exit(1)
    except Exception:
        _cleanup()
        raise

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Finished installation.{C['RST']}")
    msg()
    msg(f"{C['CYAN']}Log in with: {C['GREEN']}{PROGRAM_NAME} login {install_name}{C['CYAN']}{C['RST']}")
    msg()
