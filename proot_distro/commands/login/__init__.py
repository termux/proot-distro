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

# Architecture: Top-level `login` command. Spawns an interactive shell
# (or custom command) inside a proot container. The command body
# resolves the runtime state (rootfs, distro type, user, env, binds)
# and then exec's into proot. Helpers live in sibling submodules:
#
#   passwd.py    — user/group resolution against the rootfs.
#   bindings.py  — Android storage and system --bind args.
#   env.py       — image Env reading + /etc/profile.d injection.
#   migrate.py   — legacy installed-rootfs migration.
#   quoting.py   — POSIX double-quoting for --get-proot-cmd.

import json
import os
import shlex
import shutil
import sys

from proot_distro.constants import (
    IS_TERMUX,
    TERMUX_PREFIX,
    TERMUX_HOME,
    DEFAULT_PATH_ENV,
    DEFAULT_FAKE_KERNEL_RELEASE,
    PROGRAM_NAME,
)
from proot_distro.message import msg, crit_error
from proot_distro.arch import (
    detect_installed_arch,
    get_device_cpu_arch,
    get_emulator_args,
)
from proot_distro.sysdata import setup_fake_sysdata
from proot_distro.locking import ContainerLock
from proot_distro.names import require_valid_name
from proot_distro.paths import container_dir, container_rootfs

from proot_distro.commands.login.env import (
    IMAGE_ENV_BLOCKED, inject_termux_profile, read_manifest_env,
)
from proot_distro.commands.login.migrate import migrate_legacy_rootfs
from proot_distro.commands.login.passwd import (
    find_passwd_by_uid,
    read_group_gid,
    read_passwd_field,
    resolve_rootfs_path,
)
from proot_distro.commands.login.proot_cmd import build_proot_args
from proot_distro.commands.login.quoting import dq


def command_login(args) -> None:
    """Spawn an interactive shell (or custom command) inside the container."""
    container_name = args.container_name
    require_valid_name(container_name)

    # Acquire a shared lock before setup and exec. inheritable=True
    # clears O_CLOEXEC so the fd is kept open by proot after execvpe()
    # and the lock is held for the entire container session. When
    # execvpe() succeeds the process is replaced and __exit__ is
    # never called — intentionally, since the lock must persist in
    # proot. On any error exit __exit__ releases it.
    with ContainerLock(
        container_name, exclusive=False, command="login", inheritable=True
    ):
        _command_login_inner(container_name, args)


def _detect_dist_type(rootfs: str) -> str:
    """Return 'termux' iff the rootfs is a Termux installation.

    Check for the existence of the Termux 'login' binary as a file
    (not directory). When a concurrent proot session is running it can
    materialise the bind-mount target directory on the host filesystem;
    a directory check would then misidentify a normal container as termux.
    """
    termux_usr = rootfs + TERMUX_PREFIX
    if os.path.isfile(os.path.join(termux_usr, "bin", "login")):
        return "termux"
    return "normal"


def _resolve_login_user(rootfs: str, container_name: str, user_arg: str) -> dict:
    """Resolve --user against the container's passwd/group.

    Returns a dict with name, uid, gid, home, shell — all strings.
    Exits on invalid input (e.g. unknown name when /etc/passwd is
    present, or a non-numeric name when it isn't).
    """
    if ":" in user_arg:
        user_spec, group_spec = user_arg.split(":", 1)
        if not user_spec or not group_spec:
            crit_error("'--user' with ':' separator requires "
                       "both user and group to be non-empty.")
            sys.exit(1)
    else:
        user_spec = user_arg
        group_spec = None

    passwd_available = False
    try:
        passwd_path = resolve_rootfs_path(rootfs, "/etc/passwd")
        passwd_available = os.path.isfile(passwd_path)
    except OSError:
        pass

    if passwd_available:
        if user_spec.isdigit():
            uid = user_spec
            home, shell, primary_gid = find_passwd_by_uid(rootfs, user_spec)
            home = home or "/"
            shell = shell or "/bin/sh"
        else:
            try:
                with open(passwd_path) as fh:
                    user_found = any(
                        line.startswith(f"{user_spec}:") for line in fh
                    )
            except OSError:
                user_found = False
            if not user_found:
                crit_error(f"no user '{user_spec}' defined in /etc/passwd.")
                sys.exit(1)

            uid = read_passwd_field(rootfs, user_spec, 2)
            primary_gid = read_passwd_field(rootfs, user_spec, 3)
            home = read_passwd_field(rootfs, user_spec, 5) or "/"
            shell = read_passwd_field(rootfs, user_spec, 6) or "/bin/sh"

            if not uid:
                crit_error(f"failed to retrieve UID for user '{user_spec}'.")
                sys.exit(1)

        if group_spec is None:
            gid = primary_gid or uid
        elif group_spec.isdigit():
            gid = group_spec
        else:
            gid = read_group_gid(rootfs, group_spec)
            if not gid:
                crit_error(
                    f"no group '{group_spec}' defined in /etc/group."
                )
                sys.exit(1)
    else:
        if user_spec == "root":
            uid = "0"
        elif user_spec.isdigit():
            uid = user_spec
        else:
            crit_error(f"container '{container_name}' has no /etc/passwd; "
                       f"'--user' only accepts a numeric UID in this case.")
            sys.exit(1)
        if group_spec is None:
            gid = uid
        elif group_spec.isdigit():
            gid = group_spec
        else:
            crit_error(f"container '{container_name}' has no /etc/group; "
                       f"'--user' only accepts a numeric GID in group "
                       f"specification.")
            sys.exit(1)
        home = "/"
        shell = "/bin/sh"

    return {
        "name": user_spec,
        "uid": uid,
        "gid": gid,
        "home": home,
        "shell": shell,
    }


def _build_termux_env(extra_env, minimal):
    """Env dict for termux-type containers."""
    env: dict = {}
    termux_home_inner = TERMUX_HOME
    if not minimal:
        env["HOME"] = termux_home_inner
        env["PATH"] = f"{TERMUX_PREFIX}/bin"
        env["PREFIX"] = TERMUX_PREFIX
        env["TMPDIR"] = f"{TERMUX_PREFIX}/tmp"
    for entry in extra_env:
        key, _, val = entry.partition("=")
        if key:
            env[key] = val
    host_term = os.environ.get("TERM", "")
    if host_term:
        env["TERM"] = host_term
    host_colorterm = os.environ.get("COLORTERM", "")
    if host_colorterm:
        env["COLORTERM"] = host_colorterm
    return env


def _build_normal_env(container_path, login_user, login_home,
                      extra_env, minimal, isolated):
    """Env dict for normal-type containers."""
    env: dict = {}

    if minimal:
        for entry in extra_env:
            key, _, val = entry.partition("=")
            if key:
                env[key] = val
        env["TERM"] = os.environ.get("TERM", "") or "xterm-256color"
        host_colorterm = os.environ.get("COLORTERM", "")
        if host_colorterm:
            env["COLORTERM"] = host_colorterm
        return env

    env["PATH"] = DEFAULT_PATH_ENV
    if IS_TERMUX:
        env["MOZ_FAKE_NO_SANDBOX"] = "1"
        env["PULSE_SERVER"] = "127.0.0.1"

    for entry in read_manifest_env(container_path):
        key, _, val = entry.partition("=")
        if key and key not in IMAGE_ENV_BLOCKED:
            env[key] = val

    if IS_TERMUX and not isolated:
        for var in (
            "ANDROID_ART_ROOT", "ANDROID_DATA", "ANDROID_I18N_ROOT",
            "ANDROID_ROOT", "ANDROID_RUNTIME_ROOT",
            "ANDROID_TZDATA_ROOT",
            "BOOTCLASSPATH", "DEX2OATBOOTCLASSPATH", "EXTERNAL_STORAGE",
        ):
            val = os.environ.get(var, "")
            if val:
                env[var] = val

    for entry in extra_env:
        key, _, val = entry.partition("=")
        if key:
            env[key] = val

    env["HOME"] = login_home
    env["USER"] = login_user
    env["TERM"] = os.environ.get("TERM", "") or "xterm-256color"
    host_colorterm = os.environ.get("COLORTERM", "")
    if host_colorterm:
        env["COLORTERM"] = host_colorterm
    return env


def _check_shell_available(rootfs, container_path, login_shell, container_name):
    """Exit with a helpful error when the shell can't be resolved in the rootfs."""
    try:
        shell_found = os.path.isfile(
            resolve_rootfs_path(rootfs, login_shell)
        )
    except OSError:
        shell_found = False
    if shell_found:
        return

    has_ep_or_cmd = False
    try:
        with open(os.path.join(container_path, "manifest.json")) as fh:
            data = json.load(fh)
        cfg = (data.get("image_config") or {}).get("config", {})
        has_ep_or_cmd = bool(
            (cfg.get("Entrypoint") or []) or (cfg.get("Cmd") or [])
        )
    except (OSError, ValueError):
        pass

    if has_ep_or_cmd:
        crit_error(f"shell '{login_shell}' is not available in container "
                   f"'{container_name}'. The image defines an Entrypoint or "
                   f"Cmd; use '{PROGRAM_NAME} run {container_name}' instead.")
    else:
        crit_error(f"shell '{login_shell}' is not available in container "
                   f"'{container_name}' and the image has no Entrypoint or "
                   f"Cmd defined.")
    sys.exit(1)


def _command_login_inner(container_name: str, args) -> None:
    migrate_legacy_rootfs(container_name)

    rootfs = container_rootfs(container_name)
    if not os.path.isdir(rootfs):
        crit_error(f"container '{container_name}' is not installed.")
        sys.exit(1)

    dist_type = _detect_dist_type(rootfs)
    container_path = container_dir(container_name)

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
    use_shared_home = getattr(args, "shared_home", False)
    shared_tmp = getattr(args, "shared_tmp", False)
    shared_x11 = getattr(args, "shared_x11", False)
    no_link2symlink = getattr(args, "no_link2symlink", False)
    no_sysvipc = getattr(args, "no_sysvipc", False)
    no_kill_on_exit = getattr(args, "no_kill_on_exit", False)
    custom_binds = getattr(args, "bind", []) or []
    extra_env = getattr(args, "env", []) or []
    login_cmd = getattr(args, "login_cmd", []) or []
    run_inner = getattr(args, "_run_inner", None)

    if dist_type == "termux":
        if not login_wd:
            login_wd = TERMUX_HOME
        child_env = _build_termux_env(extra_env, minimal)

        if run_inner is not None:
            inner = run_inner
        else:
            inner = [f"{TERMUX_PREFIX}/bin/login"]
            if login_cmd:
                inner += ["-c", shlex.join(login_cmd)]
        # termux-type doesn't use uid/gid for proot --change-id.
        login_uid = login_gid = login_home = None
    else:
        user = _resolve_login_user(rootfs, container_name, login_user)
        login_user = user["name"]
        login_uid = user["uid"]
        login_gid = user["gid"]
        login_home = user["home"]
        login_shell = user["shell"]

        if not login_wd:
            login_wd = login_home

        child_env = _build_normal_env(
            container_path, login_user, login_home,
            extra_env, minimal, isolated,
        )

        if run_inner is not None:
            inner = run_inner
        else:
            _check_shell_available(rootfs, container_path, login_shell, container_name)
            if login_cmd:
                inner = [login_shell, "-c", shlex.join(login_cmd)]
            else:
                inner = [login_shell, "-l"]

        if IS_TERMUX and not minimal:
            setup_fake_sysdata(rootfs)

    # Ensure Termux bin is always last in PATH so guest tools can
    # invoke host Termux utilities. Skipped in --isolated and --minimal
    # modes where TERMUX_PREFIX is not bound into the guest. Dedupes any
    # existing occurrence first.
    if IS_TERMUX and not isolated and not minimal:
        termux_bin = f"{TERMUX_PREFIX}/bin"
        components = [
            c for c in child_env.get("PATH", "").split(":")
            if c and c != termux_bin
        ]
        components.append(termux_bin)
        child_env["PATH"] = ":".join(components)

    if dist_type == "normal" and IS_TERMUX and not isolated and not minimal:
        inject_termux_profile(rootfs, child_env)

    # Architecture detection.
    target_arch = detect_installed_arch(rootfs)
    if target_arch == "unknown":
        target_arch = get_device_cpu_arch()

    device_arch = get_device_cpu_arch()

    emulator_override = getattr(args, "emulator", None) or ""
    emu_args = get_emulator_args(target_arch, device_arch, emulator_override)
    need_emu = bool(emu_args)

    if dist_type == "termux" and need_emu:
        crit_error(f"cannot run Termux-type container '{container_name}' "
                   f"under emulation. The container architecture is "
                   f"'{target_arch}' but the host is '{device_arch}'. "
                   f"Termux-type containers are not emulatable "
                   f"because the host and the container share the same "
                   f"Termux prefix path ({TERMUX_PREFIX}), so the host "
                   f"binaries at that path would be visible inside the "
                   f"container instead of the container's own "
                   f"architecture-specific binaries.")
        sys.exit(1)

    proot_bin = shutil.which("proot") or "proot"
    proot_args = build_proot_args(
        proot_bin=proot_bin,
        rootfs=rootfs,
        login_wd=login_wd,
        login_uid=login_uid,
        login_gid=login_gid,
        login_home=login_home,
        emu_args=emu_args,
        need_emu=need_emu,
        target_arch=target_arch,
        hostname=hostname,
        kernel_release=kernel_release,
        dist_type=dist_type,
        minimal=minimal,
        isolated=isolated,
        no_link2symlink=no_link2symlink,
        no_sysvipc=no_sysvipc,
        no_kill_on_exit=no_kill_on_exit,
        use_shared_home=use_shared_home,
        shared_tmp=shared_tmp,
        shared_x11=shared_x11,
        custom_binds=custom_binds,
        redirect_ports=redirect_ports,
        inner=inner,
    )

    # Proot itself reads a few env vars to toggle its own behavior. They
    # are passed through to proot (and therefore to the spawned guest
    # process). In minimal mode only PROOT_L2S_DIR is exported; proot
    # debug vars are skipped to keep the environment truly minimal.
    if not minimal:
        for var in ("PROOT_NO_SECCOMP", "PROOT_VERBOSE"):
            val = os.environ.get(var)
            if val:
                child_env[var] = val
    if IS_TERMUX and dist_type != "termux":
        # Always pin PROOT_L2S_DIR to a fixed path and create the
        # directory upfront. Without this the first session lets proot
        # choose the location implicitly while a simultaneous session
        # sets it explicitly — they can race when both start at once.
        l2s_dir = os.path.join(rootfs, ".l2s")
        os.makedirs(l2s_dir, exist_ok=True)
        child_env["PROOT_L2S_DIR"] = l2s_dir
    child_env.pop("LD_PRELOAD", None)

    if getattr(args, "get_proot_cmd", False):
        parts = ["env", "-i"]
        for k, v in child_env.items():
            parts.append(f"{k}={dq(v)}")
        parts.extend(dq(a) for a in proot_args)
        print(" \\\n  ".join(parts))
        sys.exit(0)

    os.execvpe(proot_bin, proot_args, child_env)


__all__ = ("command_login",)
