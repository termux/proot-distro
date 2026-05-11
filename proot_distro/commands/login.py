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

# Architecture: Spawns an interactive shell (or custom command) inside a
# proot container. Builds the full proot command line including all bindings,
# emulator selection, and environment variable setup, then exec's into proot.
# Legacy installed-rootfs paths are migrated to the new containers layout on
# first login. Architecture is detected from ELF headers, not config files.

import errno
import json
import os
import re
import shlex
import shutil
import stat
import sys

from proot_distro.constants import (
    CONTAINERS_DIR,
    LEGACY_ROOTFS_DIR,
    PREFIX,
    TERMUX_HOME,
    TERMUX_APP_PACKAGE,
    DEFAULT_PATH_ENV,
    DEFAULT_FAKE_KERNEL_RELEASE,
    DEFAULT_FAKE_KERNEL_VERSION,
    PROGRAM_NAME,
)
from proot_distro.colors import C, msg
from proot_distro.arch import (
    get_device_cpu_arch,
    detect_installed_arch,
    supports_32bit,
    get_emulator_args,
)
from proot_distro.sysdata import setup_fake_sysdata, fake_proc_bindings


_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "_-+./:@="
)


def _dq(s: str) -> str:
    """Return s in double quotes for a POSIX shell, quoting only when needed."""
    if s and all(c in _SAFE_CHARS for c in s):
        return s
    escaped = (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("$", "\\$")
         .replace("`", "\\`")
    )
    return f'"{escaped}"'


def _resolve_rootfs_path(rootfs: str, guest_path: str) -> str:
    """Resolve an absolute guest path to its real host path.

    Follows symlinks within the rootfs namespace. Absolute symlink targets
    are re-rooted under rootfs to handle images (e.g. Nix) where /etc/passwd
    is a symlink to an absolute store path that only exists inside the guest.

    Raises OSError if the path is missing, a target is missing, or the chain
    exceeds 40 levels (ELOOP).
    """
    for _ in range(40):
        host_path = rootfs + guest_path
        try:
            st = os.lstat(host_path)
        except OSError:
            raise
        if not stat.S_ISLNK(st.st_mode):
            return host_path
        target = os.readlink(host_path)
        if os.path.isabs(target):
            guest_path = os.path.normpath(target)
        else:
            guest_path = os.path.normpath(
                os.path.join(os.path.dirname(guest_path), target)
            )
    raise OSError(errno.ELOOP, "Too many levels of symbolic links", guest_path)


def _read_passwd_field(rootfs: str, user: str, field_index: int) -> str:
    try:
        passwd = _resolve_rootfs_path(rootfs, "/etc/passwd")
    except OSError:
        return ""
    try:
        with open(passwd) as fh:
            for line in fh:
                parts = line.strip().split(":")
                if parts and parts[0] == user and len(parts) > field_index:
                    return parts[field_index]
    except OSError:
        pass
    return ""


# Variables that the image Env must not override. These are either
# proot-distro-defined values or host-inherited terminal variables that must
# remain under the launcher's control regardless of image configuration.
_IMAGE_ENV_BLOCKED = frozenset({
    "ANDROID_ART_ROOT", "ANDROID_DATA", "ANDROID_I18N_ROOT",
    "ANDROID_ROOT", "ANDROID_RUNTIME_ROOT", "ANDROID_TZDATA_ROOT",
    "BOOTCLASSPATH", "DEX2OATBOOTCLASSPATH", "EXTERNAL_STORAGE",
    "MOZ_FAKE_NO_SANDBOX", "PULSE_SERVER",
    "TERM", "COLORTERM",
})


def _read_manifest_env(container_dir: str) -> list:
    """Return image Env entries from manifest.json, or [] if absent/invalid."""
    manifest_path = os.path.join(container_dir, "manifest.json")
    try:
        with open(manifest_path) as fh:
            data = json.load(fh)
        env = (data.get("image_config") or {}).get("config", {}).get("Env") or []
        return [e for e in env if isinstance(e, str) and "=" in e]
    except (OSError, ValueError):
        return []


def _storage_bindings() -> list:
    """Return --bind args for Android shared storage."""
    binds = []
    if os.access("/storage", os.R_OK):
        binds += ["--bind=/storage", "--bind=/storage/emulated/0:/sdcard"]
    else:
        for p in ("/storage/self/primary", "/storage/emulated/0", "/sdcard"):
            if os.access(p, os.R_OK):
                binds += [
                    f"--bind={p}:/sdcard",
                    f"--bind={p}:/storage/emulated/0",
                    f"--bind={p}:/storage/self/primary",
                ]
                break
    return binds


def _system_bindings() -> list:
    """Return --bind args for Android system paths."""
    binds = []
    for path in (
        "/apex", "/odm", "/product", "/system", "/system_ext", "/vendor",
        "/linkerconfig/ld.config.txt",
        "/linkerconfig/com.android.art/ld.config.txt",
        "/plat_property_contexts", "/property_contexts",
    ):
        try:
            real = os.path.realpath(path)
        except OSError:
            continue
        if not os.path.exists(real):
            continue
        if os.path.isdir(real):
            mode = oct(os.stat(real).st_mode)[-1]
            if mode in ("1", "5", "7"):
                binds.append(f"--bind={real}")
        elif os.path.isfile(real):
            try:
                with open(real, "rb") as fh:
                    fh.read(1)
                binds.append(f"--bind={real}")
            except OSError:
                pass
    return binds


def _migrate_legacy_rootfs(dist_name: str) -> None:
    """Move legacy installed-rootfs/<name> to containers/<name>/rootfs."""
    legacy_path = os.path.join(LEGACY_ROOTFS_DIR, dist_name)
    if not os.path.isdir(legacy_path):
        return

    container_dir = os.path.join(CONTAINERS_DIR, dist_name)
    new_rootfs = os.path.join(container_dir, "rootfs")

    if os.path.isdir(new_rootfs):
        return  # already migrated

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Migrating legacy container "
        f"'{C['YELLOW']}{dist_name}{C['CYAN']}'...{C['RST']}")
    try:
        os.makedirs(container_dir, exist_ok=True)
        os.rename(legacy_path, new_rootfs)
    except OSError as exc:
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Migration failed: {exc}{C['RST']}")
        return

    # Rewrite l2s symlinks whose targets still point at the old rootfs path.
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Updating PRoot link2symlink extension files "
        f"(may take a long time)...{C['RST']}")
    for dirpath, _dirs, filenames in os.walk(new_rootfs):
        for fname in filenames:
            fpath = os.path.join(dirpath, fname)
            try:
                if os.path.islink(fpath):
                    target = os.readlink(fpath)
                    if target.startswith(legacy_path):
                        new_target = new_rootfs + target[len(legacy_path):]
                        os.unlink(fpath)
                        os.symlink(new_target, fpath)
            except OSError:
                pass

    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
        f"Migration complete.{C['RST']}")


def _inject_termux_profile(rootfs: str) -> None:
    """Write a profile.d snippet that appends Termux bin to PATH.

    Login shells source /etc/profile which overwrites PATH from scratch,
    discarding whatever proot inherited. Dropping a snippet into profile.d
    ensures the Termux bin dir survives that reset without modifying any
    distro-owned file.
    """
    profile_d = os.path.join(rootfs, "etc", "profile.d")
    if not os.path.isdir(profile_d):
        return
    snippet = os.path.join(profile_d, "termux-prefix.sh")
    termux_bin = f"{PREFIX}/bin"
    content = (
        f'case ":${{PATH}}:" in\n'
        f'  *":{termux_bin}:"*) ;;\n'
        f'  *) export PATH="${{PATH}}:{termux_bin}" ;;\n'
        f'esac\n'
    )
    try:
        with open(snippet, "w") as fh:
            fh.write(content)
        os.chmod(snippet, 0o644)
    except OSError:
        pass


_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-]*$')


def command_login(args, configs: dict) -> None:  # noqa: ARG001
    dist_name = args.alias

    if not _NAME_RE.match(dist_name):
        msg()
        msg(f"{C['BRED']}Error: container name "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}' is not valid. "
            f"It must begin with a letter or digit and contain only "
            f"letters, digits, underscores, dots, or hyphens.{C['RST']}")
        msg()
        sys.exit(1)

    # Migrate legacy rootfs layout on first login if applicable.
    _migrate_legacy_rootfs(dist_name)

    rootfs = os.path.join(CONTAINERS_DIR, dist_name, "rootfs")
    if not os.path.isdir(rootfs):
        msg()
        msg(f"{C['BRED']}Error: container "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}' is not installed.{C['RST']}")
        msg()
        sys.exit(1)

    _termux_usr = os.path.join(
        rootfs, "data", "data", "com.termux", "files", "usr"
    )
    # Check for a specific binary rather than just directory presence.
    # When another session is already running, proot may create the bind-mount
    # target directory (<rootfs>/data/data/com.termux/files/usr/) on the host
    # filesystem as an empty directory. Checking only isdir() would then
    # incorrectly classify a normal container as termux type.
    dist_type = (
        "termux"
        if os.path.isfile(os.path.join(_termux_usr, "bin", "login"))
        else "normal"
    )

    login_user = getattr(args, "user", "root") or "root"
    kernel_release = (
        getattr(args, "kernel", DEFAULT_FAKE_KERNEL_RELEASE)
        or DEFAULT_FAKE_KERNEL_RELEASE
    )
    hostname = getattr(args, "hostname", "localhost") or "localhost"
    login_wd = getattr(args, "work_dir", "") or ""
    redirect_ports = getattr(args, "redirect_ports", False)
    isolated = getattr(args, "isolated", False)
    minimal = getattr(args, "minimal", False)
    use_termux_home = getattr(args, "termux_home", False)
    shared_tmp = getattr(args, "shared_tmp", False)
    shared_x11 = getattr(args, "shared_x11", False)
    no_link2symlink = getattr(args, "no_link2symlink", False)
    no_sysvipc = getattr(args, "no_sysvipc", False)
    no_kill_on_exit = getattr(args, "no_kill_on_exit", False)
    no_arch_warning = getattr(args, "no_arch_warning", False)
    custom_binds = getattr(args, "bind", []) or []
    extra_env = getattr(args, "env", []) or []
    login_cmd = getattr(args, "login_cmd", []) or []

    _TERMUX_FILES = "/data/data/com.termux/files"
    _TERMUX_USR = f"{_TERMUX_FILES}/usr"
    _TERMUX_HOME_INNER = f"{_TERMUX_FILES}/home"

    # Environment variables that will be inherited by proot and then by the
    # spawned guest process. Insertion order matters: later assignments win.
    child_env: dict = {}

    if dist_type == "termux":
        if not login_wd:
            login_wd = _TERMUX_HOME_INNER
        if minimal:
            for entry in extra_env:
                key, _, val = entry.partition("=")
                if key:
                    child_env[key] = val
        else:
            child_env["HOME"] = _TERMUX_HOME_INNER
            child_env["PATH"] = f"{_TERMUX_USR}/bin"
            child_env["PREFIX"] = _TERMUX_USR
            child_env["TMPDIR"] = f"{_TERMUX_USR}/tmp"
            for entry in extra_env:
                key, _, val = entry.partition("=")
                if key:
                    child_env[key] = val
        host_term = os.environ.get("TERM", "")
        if host_term:
            child_env["TERM"] = host_term
        host_colorterm = os.environ.get("COLORTERM", "")
        if host_colorterm:
            child_env["COLORTERM"] = host_colorterm
        run_inner = getattr(args, "_run_inner", None)
        if run_inner is not None:
            inner = run_inner
        else:
            inner = [f"{_TERMUX_USR}/bin/login"]
            if login_cmd:
                inner += ["-c", shlex.join(login_cmd)]
        login_uid = login_gid = login_home = None
    else:
        # /etc/passwd is optional. When absent, --user must be a numeric UID.
        passwd_available = False
        try:
            passwd_path = _resolve_rootfs_path(rootfs, "/etc/passwd")
            passwd_available = os.path.isfile(passwd_path)
        except OSError:
            pass

        if passwd_available:
            try:
                with open(passwd_path) as fh:
                    user_found = any(
                        line.startswith(f"{login_user}:") for line in fh
                    )
            except OSError:
                user_found = False

            if not user_found:
                msg(f"{C['BRED']}Error: no user "
                    f"'{C['YELLOW']}{login_user}{C['BRED']}' defined in "
                    f"/etc/passwd.{C['RST']}")
                sys.exit(1)

            login_uid = _read_passwd_field(rootfs, login_user, 2)
            login_gid = _read_passwd_field(rootfs, login_user, 3)
            login_home = _read_passwd_field(rootfs, login_user, 5)
            login_shell = _read_passwd_field(rootfs, login_user, 6)

            if not login_uid:
                msg(f"{C['BRED']}Error: failed to retrieve UID for user "
                    f"'{login_user}'.{C['RST']}")
                sys.exit(1)
            if not login_home:
                login_home = (
                    f"/home/{login_user}" if login_user != "root" else "/root"
                )
            if not login_wd:
                login_wd = login_home
            if not login_shell:
                login_shell = "/bin/sh"
        else:
            # No /etc/passwd: accept only a numeric UID via --user.
            # "root" is allowed as a universal alias for UID 0.
            if login_user == "root":
                login_uid = login_gid = "0"
            elif login_user.isdigit():
                login_uid = login_gid = login_user
            else:
                msg()
                msg(f"{C['BRED']}Error: container "
                    f"'{C['YELLOW']}{dist_name}{C['BRED']}' has no "
                    f"/etc/passwd; '--user' only accepts a numeric UID "
                    f"in this case.{C['RST']}")
                msg()
                sys.exit(1)
            login_home = "/root" if login_uid == "0" else f"/home/{login_uid}"
            if not login_wd:
                login_wd = login_home
            login_shell = "/bin/sh"

        container_dir = os.path.dirname(rootfs)

        if minimal:
            # Bare-minimum env: only --env args plus terminal variables.
            for entry in extra_env:
                key, _, val = entry.partition("=")
                if key:
                    child_env[key] = val
            child_env["TERM"] = os.environ.get("TERM", "") or "xterm-256color"
            host_colorterm = os.environ.get("COLORTERM", "")
            if host_colorterm:
                child_env["COLORTERM"] = host_colorterm
        else:
            # Baseline guest env. Always exported on every login.
            child_env["PATH"] = DEFAULT_PATH_ENV
            child_env["MOZ_FAKE_NO_SANDBOX"] = "1"
            child_env["PULSE_SERVER"] = "127.0.0.1"

            # Image-defined Env entries. Blocked vars cannot be overridden.
            for entry in _read_manifest_env(container_dir):
                key, _, val = entry.partition("=")
                if key and key not in _IMAGE_ENV_BLOCKED:
                    child_env[key] = val

            # Android system vars exported only when not running --isolated.
            if not isolated:
                for var in (
                    "ANDROID_ART_ROOT", "ANDROID_DATA", "ANDROID_I18N_ROOT",
                    "ANDROID_ROOT", "ANDROID_RUNTIME_ROOT",
                    "ANDROID_TZDATA_ROOT",
                    "BOOTCLASSPATH", "DEX2OATBOOTCLASSPATH", "EXTERNAL_STORAGE",
                ):
                    val = os.environ.get(var, "")
                    if val:
                        child_env[var] = val

            # User-supplied --env=VAR=VALUE entries.
            for entry in extra_env:
                key, _, val = entry.partition("=")
                if key:
                    child_env[key] = val

            # Per-user identity and host-inherited terminal vars (always last).
            child_env["HOME"] = login_home
            child_env["USER"] = login_user
            child_env["TERM"] = os.environ.get("TERM", "") or "xterm-256color"
            host_colorterm = os.environ.get("COLORTERM", "")
            if host_colorterm:
                child_env["COLORTERM"] = host_colorterm

        run_inner = getattr(args, "_run_inner", None)
        if run_inner is not None:
            inner = run_inner
        else:
            # Verify the shell exists inside the container before exec'ing.
            shell_found = False
            try:
                shell_found = os.path.isfile(
                    _resolve_rootfs_path(rootfs, login_shell)
                )
            except OSError:
                pass

            if not shell_found:
                _mc_path = os.path.join(container_dir, "manifest.json")
                _has_ep_or_cmd = False
                try:
                    with open(_mc_path) as _fh:
                        _mc = json.load(_fh)
                    _cfg = (_mc.get("image_config") or {}).get("config", {})
                    _has_ep_or_cmd = bool(
                        (_cfg.get("Entrypoint") or [])
                        or (_cfg.get("Cmd") or [])
                    )
                except (OSError, ValueError):
                    pass

                msg()
                if _has_ep_or_cmd:
                    msg(f"{C['BRED']}Error: shell "
                        f"'{C['YELLOW']}{login_shell}{C['BRED']}' is not "
                        f"available in container "
                        f"'{C['YELLOW']}{dist_name}{C['BRED']}'. The image "
                        f"defines an Entrypoint or Cmd; use "
                        f"'{C['YELLOW']}{PROGRAM_NAME} run "
                        f"{dist_name}{C['BRED']}' instead.{C['RST']}")
                else:
                    msg(f"{C['BRED']}Error: shell "
                        f"'{C['YELLOW']}{login_shell}{C['BRED']}' is not "
                        f"available in container "
                        f"'{C['YELLOW']}{dist_name}{C['BRED']}', and the "
                        f"image has no Entrypoint or Cmd defined.{C['RST']}")
                msg()
                sys.exit(1)

            if login_cmd:
                inner = [login_shell, "-c", shlex.join(login_cmd)]
            else:
                inner = [login_shell, "-l"]

        if not minimal:
            setup_fake_sysdata(rootfs)

    # Ensure Termux bin is always last in PATH so guest tools can invoke
    # host Termux utilities. Skipped in --isolated and --minimal modes where
    # PREFIX is not bound into the guest. De-duplicates any existing
    # occurrence first.
    if not isolated and not minimal:
        termux_bin = f"{PREFIX}/bin"
        components = [
            c for c in child_env.get("PATH", "").split(":")
            if c and c != termux_bin
        ]
        components.append(termux_bin)
        child_env["PATH"] = ":".join(components)

    if dist_type == "normal" and not isolated and not minimal:
        _inject_termux_profile(rootfs)

    # Architecture detection.
    target_arch = detect_installed_arch(rootfs)
    if target_arch == "unknown":
        target_arch = get_device_cpu_arch()

    device_arch = get_device_cpu_arch()

    if (target_arch != device_arch and not no_arch_warning
            and not supports_32bit()):
        msg(f"{C['BRED']}Warning: CPU doesn't support 32-bit instructions, "
            f"some software may not work.{C['RST']}")

    emulator_override = getattr(args, "emulator", None) or ""
    emu_args = get_emulator_args(target_arch, device_arch, emulator_override)
    need_emu = bool(emu_args)

    if dist_type == "termux" and need_emu:
        msg()
        msg(f"{C['BRED']}Error: cannot run Termux-type container "
            f"'{C['YELLOW']}{dist_name}{C['BRED']}' under emulation. "
            f"The container architecture is '{C['YELLOW']}{target_arch}"
            f"{C['BRED']}' but the host is '{C['YELLOW']}{device_arch}"
            f"{C['BRED']}'. Termux-type containers are not emulatable because "
            f"the host and the container share the same Termux prefix path "
            f"({PREFIX}), so the host binaries at that path would be "
            f"visible inside the container instead of the container's own "
            f"architecture-specific binaries.{C['RST']}")
        msg()
        sys.exit(1)

    proot_bin = shutil.which("proot") or "proot"
    proot_args = [proot_bin] + emu_args

    if not no_kill_on_exit:
        proot_args.append("--kill-on-exit")
    else:
        msg(f"{C['BRED']}Warning: option "
            f"'{C['YELLOW']}--no-kill-on-exit{C['BRED']}' is enabled. "
            f"When exiting, your session will be blocked until all processes "
            f"are terminated.{C['RST']}")

    if dist_type != "termux" and not no_link2symlink:
        proot_args.append("--link2symlink")

    if not no_sysvipc:
        proot_args.append("--sysvipc")

    _ARCH_UNAME_M = {
        "aarch64": "aarch64",
        "arm":     "armv7l",
        "i686":    "i686",
        "x86_64":  "x86_64",
        "riscv64": "riscv64",
    }
    if not minimal:
        uname_m = _ARCH_UNAME_M.get(target_arch, os.uname().machine)
        proot_args.append(
            f"--kernel-release=\\Linux\\{hostname}\\{kernel_release}"
            f"\\{DEFAULT_FAKE_KERNEL_VERSION}\\{uname_m}\\localdomain\\-1\\"
        )

    proot_args.append("-L")  # Fix lstat for dpkg symlink warnings.

    if dist_type != "termux":
        proot_args.append(f"--change-id={login_uid}:{login_gid}")

    proot_args.append(f"--rootfs={rootfs}")
    proot_args.append(f"--cwd={login_wd}")

    proot_args += ["--bind=/dev", "--bind=/proc", "--bind=/sys"]

    if not minimal:
        if dist_type != "termux":
            proot_args += [
                "--bind=/dev/urandom:/dev/random",
                "--bind=/proc/self/fd:/dev/fd",
            ]
            for i, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
                if os.path.exists(f"/proc/self/fd/{i}"):
                    proot_args.append(f"--bind=/proc/self/fd/{i}:/dev/{name}")

            proot_args.append(f"--bind={rootfs}/sys/.empty:/sys/fs/selinux")
            proot_args += fake_proc_bindings(rootfs)

            tmp_dir = os.path.join(rootfs, "tmp")
            os.makedirs(tmp_dir, exist_ok=True)
            try:
                os.chmod(tmp_dir, 0o1777)
            except OSError:
                pass
            proot_args.append(f"--bind={tmp_dir}:/dev/shm")

        if not isolated:
            for data_dir in (
                "/data/app", "/data/dalvik-cache",
                "/data/misc/apexdata/com.android.art/dalvik-cache",
            ):
                if not os.path.isdir(data_dir):
                    continue
                mode = oct(os.stat(data_dir).st_mode)[-1]
                if mode in ("1", "5", "7"):
                    proot_args.append(f"--bind={data_dir}")

            apps_dir = f"/data/data/{TERMUX_APP_PACKAGE}/files/apps"
            if os.path.isdir(apps_dir):
                proot_args.append(f"--bind={apps_dir}")

            if dist_type != "termux":
                proot_args.append(
                    f"--bind=/data/data/{TERMUX_APP_PACKAGE}/cache"
                )
                proot_args.append(f"--bind={TERMUX_HOME}")
            else:
                os.makedirs(
                    os.path.join(
                        rootfs, "data", "data", "com.termux", "cache"
                    ),
                    exist_ok=True,
                )

            proot_args += _storage_bindings()

        if dist_type == "termux" or not isolated or need_emu:
            proot_args += _system_bindings()
            if dist_type != "termux":
                proot_args.append(f"--bind={PREFIX}")

        if use_termux_home:
            if dist_type == "termux":
                proot_args.append(f"--bind={TERMUX_HOME}:{_TERMUX_HOME_INNER}")
            elif login_user == "root":
                proot_args.append(f"--bind={TERMUX_HOME}:/root")
            else:
                proot_args.append(f"--bind={TERMUX_HOME}:{login_home}")

        if shared_tmp and dist_type != "termux":
            proot_args.append(f"--bind={PREFIX}/tmp:/tmp")

        if shared_x11 and dist_type != "termux":
            proot_args.append(f"--bind={PREFIX}/tmp/.X11-unix:/tmp/.X11-unix")

    for bnd in custom_binds:
        if ":" in bnd:
            src, dst = bnd.split(":", 1)
        else:
            src, dst = bnd, None

        src = os.path.abspath(src)

        if dst in (".", ".."):
            msg()
            msg(f"{C['BRED']}Error: '.' and '..' are not allowed as binding "
                f"destination.{C['RST']}")
            msg()
            sys.exit(1)

        proot_args.append(
            f"--bind={src}:{dst}" if dst else f"--bind={src}"
        )

    if redirect_ports:
        proot_args.append("-p")

    proot_args += inner

    # Proot itself reads a few env vars to toggle its own behavior. They are
    # passed through to proot (and therefore to the spawned guest process).
    # In minimal mode only PROOT_L2S_DIR is exported; proot debug vars are
    # skipped to keep the environment truly minimal.
    if not minimal:
        for var in ("PROOT_NO_SECCOMP", "PROOT_DUMP", "PROOT_VERBOSE"):
            val = os.environ.get(var)
            if val:
                child_env[var] = val
    if dist_type != "termux":
        # Always pin PROOT_L2S_DIR to a fixed path and create the directory
        # upfront. Without this, the first session lets proot choose the
        # location implicitly while simultaneous sessions set it explicitly,
        # which can cause conflicts when both instances start at the same time.
        l2s_dir = os.path.join(rootfs, ".l2s")
        os.makedirs(l2s_dir, exist_ok=True)
        child_env["PROOT_L2S_DIR"] = l2s_dir
    child_env.pop("LD_PRELOAD", None)

    debug = getattr(args, "debug", False)
    if debug:
        parts = ["env"]
        for k, v in child_env.items():
            parts.append(f"{k}={_dq(v)}")
        parts.extend(_dq(a) for a in proot_args)
        cmd_line = " \\\n  ".join(parts)
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Proot command line:{C['RST']}")
        msg(cmd_line)
        sys.exit(0)

    os.execvpe(proot_bin, proot_args, child_env)
