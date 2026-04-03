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
import shlex
import stat
import sys

from proot_distro.constants import (
    INSTALLED_ROOTFS_DIR,
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


def _read_passwd_field(rootfs: str, user: str, field_index: int) -> str:
    passwd = os.path.join(rootfs, "etc", "passwd")
    try:
        with open(passwd) as fh:
            for line in fh:
                parts = line.strip().split(":")
                if parts and parts[0] == user and len(parts) > field_index:
                    return parts[field_index]
    except OSError:
        pass
    return ""


def _update_android_env_in_environment(rootfs: str) -> None:
    env_path = os.path.join(rootfs, "etc", "environment")
    try:
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass

    android_vars = (
        "ANDROID_ART_ROOT", "ANDROID_DATA", "ANDROID_I18N_ROOT",
        "ANDROID_ROOT", "ANDROID_RUNTIME_ROOT", "ANDROID_TZDATA_ROOT",
        "BOOTCLASSPATH", "DEX2OATBOOTCLASSPATH",
    )

    try:
        with open(env_path) as fh:
            lines = fh.readlines()
    except OSError:
        return

    updated = {var: os.environ.get(var, "") for var in android_vars}
    new_lines = [l for l in lines if not any(l.startswith(f"{v}=") for v in android_vars)]
    for var, val in updated.items():
        if val:
            new_lines.append(f"{var}={val}\n")

    with open(env_path, "w") as fh:
        fh.writelines(new_lines)


def _read_environment_vars(rootfs: str) -> list:
    env_path = os.path.join(rootfs, "etc", "environment")
    result = []
    try:
        with open(env_path) as fh:
            for line in fh:
                line = line.strip()
                # Strip surrounding quotes, skip empty/non-assignment lines.
                if not line or "=" not in line:
                    continue
                key, _, val = line.partition("=")
                val = val.strip("'\"")
                if key and val:
                    result.append(f"{key}={val}")
    except OSError:
        pass
    return result


def _storage_bindings() -> list:
    """Return --bind args for Android shared storage."""
    binds = []
    if os.access("/storage", os.R_OK):
        binds += ["--bind=/storage", "--bind=/storage/emulated/0:/sdcard"]
    else:
        for p in ("/storage/self/primary", "/storage/emulated/0", "/sdcard"):
            if os.access(p, os.R_OK):
                binds += [f"--bind={p}:/sdcard",
                          f"--bind={p}:/storage/emulated/0",
                          f"--bind={p}:/storage/self/primary"]
                break
    return binds


def _system_bindings(force: bool = False) -> list:
    """Return --bind args for Android system paths (apex, system, vendor, …)."""
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


def command_login(args, configs: dict) -> None:
    dist_name = args.alias

    if dist_name not in configs:
        msg()
        msg(f"{C['BRED']}Error: unknown distribution '{C['YELLOW']}{dist_name}{C['BRED']}' was requested for logging in.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}View supported distributions by: {C['GREEN']}{PROGRAM_NAME} list{C['RST']}")
        msg()
        sys.exit(1)

    rootfs = os.path.join(INSTALLED_ROOTFS_DIR, dist_name)
    if not os.path.isdir(rootfs):
        msg()
        msg(f"{C['BRED']}Error: distribution '{C['YELLOW']}{dist_name}{C['BRED']}' is not installed.{C['RST']}")
        msg()
        sys.exit(1)

    cfg = configs[dist_name]
    dist_type = cfg.dist_type

    login_user = getattr(args, "user", "root") or "root"
    kernel_release = getattr(args, "kernel", DEFAULT_FAKE_KERNEL_RELEASE) or DEFAULT_FAKE_KERNEL_RELEASE
    hostname = getattr(args, "hostname", "localhost") or "localhost"
    login_wd = getattr(args, "work_dir", "") or ""
    redirect_ports = getattr(args, "redirect_ports", False)
    isolated = getattr(args, "isolated", False)
    use_termux_home = getattr(args, "termux_home", False)
    shared_tmp = getattr(args, "shared_tmp", False)
    no_link2symlink = getattr(args, "no_link2symlink", False)
    no_sysvipc = getattr(args, "no_sysvipc", False)
    no_kill_on_exit = getattr(args, "no_kill_on_exit", False)
    no_arch_warning = getattr(args, "no_arch_warning", False)
    custom_binds = getattr(args, "bind", []) or []
    extra_env = getattr(args, "env", []) or []
    login_cmd = getattr(args, "login_cmd", []) or []

    # Constants for the Termux filesystem layout inside the distro rootfs.
    _TERMUX_FILES = "/data/data/com.termux/files"
    _TERMUX_USR = f"{_TERMUX_FILES}/usr"
    _TERMUX_HOME_INNER = f"{_TERMUX_FILES}/home"

    if dist_type == "termux":
        if not login_wd:
            login_wd = _TERMUX_HOME_INNER
        inner = [
            f"{_TERMUX_USR}/bin/env",
            f"HOME={_TERMUX_HOME_INNER}",
            f"PATH={_TERMUX_USR}/bin",
            f"PREFIX={_TERMUX_USR}",
            f"TMPDIR={_TERMUX_USR}/tmp",
            f"{_TERMUX_USR}/bin/login",
        ]
        if login_cmd:
            inner += ["-c", shlex.join(login_cmd)]
        login_uid = login_gid = login_home = None
    else:
        # Validate user and read passwd fields.
        passwd_path = os.path.join(rootfs, "etc", "passwd")
        if not os.path.isfile(passwd_path):
            msg(f"{C['BRED']}Error: the selected distribution doesn't have /etc/passwd.{C['RST']}")
            sys.exit(1)

        if not any(l.startswith(f"{login_user}:") for l in open(passwd_path)):
            msg(f"{C['BRED']}Error: no user '{C['YELLOW']}{login_user}{C['BRED']}' defined in /etc/passwd.{C['RST']}")
            sys.exit(1)

        login_uid = _read_passwd_field(rootfs, login_user, 2)
        login_gid = _read_passwd_field(rootfs, login_user, 3)
        login_home = _read_passwd_field(rootfs, login_user, 5)
        login_shell = _read_passwd_field(rootfs, login_user, 6)

        if not login_uid:
            msg(f"{C['BRED']}Error: failed to retrieve UID for user '{login_user}'.{C['RST']}")
            sys.exit(1)
        if not login_home:
            login_home = f"/home/{login_user}" if login_user != "root" else "/root"
        if not login_wd:
            login_wd = login_home
        if not login_shell:
            login_shell = "/bin/sh"

        # Update /etc/environment with current Android vars.
        _update_android_env_in_environment(rootfs)

        # Build environment variables list.
        env_vars = [f"PATH={DEFAULT_PATH_ENV}"]
        env_vars += _read_environment_vars(rootfs)
        for var in ("ANDROID_ART_ROOT", "ANDROID_DATA", "ANDROID_I18N_ROOT",
                    "ANDROID_ROOT", "ANDROID_RUNTIME_ROOT", "ANDROID_TZDATA_ROOT",
                    "BOOTCLASSPATH", "DEX2OATBOOTCLASSPATH", "EXTERNAL_STORAGE"):
            val = os.environ.get(var, "")
            if val:
                env_vars.append(f"{var}={val}")
        env_vars += extra_env

        if login_cmd:
            shell_args = ["-c", shlex.join(login_cmd)]
        else:
            shell_args = ["-l"]

        inner = (
            ["/usr/bin/env", "-i"]
            + env_vars
            + [f"COLORTERM={os.environ.get('COLORTERM', '')}",
               f"HOME={login_home}",
               f"USER={login_user}",
               f"TERM={os.environ.get('TERM', 'xterm-256color')}",
               login_shell]
            + shell_args
        )

        setup_fake_sysdata(dist_name)

    # Architecture detection.
    target_arch = detect_installed_arch(dist_name)
    if target_arch == "unknown":
        target_arch = get_device_cpu_arch()

    device_arch = get_device_cpu_arch()

    if target_arch != device_arch and not no_arch_warning and not supports_32bit():
        msg(f"{C['BRED']}Warning: CPU doesn't support 32-bit instructions, some software may not work.{C['RST']}")

    emulator_override = getattr(args, "emulator", None) or ""
    emu_args = get_emulator_args(target_arch, device_arch, emulator_override)
    need_emu = bool(emu_args)

    # Core proot flags.
    proot_args = ["proot"] + emu_args

    if not no_kill_on_exit:
        proot_args.append("--kill-on-exit")
    else:
        msg(f"{C['BRED']}Warning: option '{C['YELLOW']}--no-kill-on-exit{C['BRED']}' is enabled. "
            f"When exiting, your session will be blocked until all processes are terminated.{C['RST']}")

    if dist_type != "termux" and not no_link2symlink:
        proot_args.append("--link2symlink")

    if not no_sysvipc:
        proot_args.append("--sysvipc")

    uname_m = os.uname().machine
    proot_args.append(
        f"--kernel-release=\\Linux\\{hostname}\\{kernel_release}"
        f"\\{DEFAULT_FAKE_KERNEL_VERSION}\\{uname_m}\\localdomain\\-1\\"
    )

    proot_args.append("-L")  # Fix lstat for dpkg symlink warnings.

    if dist_type != "termux":
        proot_args.append(f"--change-id={login_uid}:{login_gid}")

    proot_args.append(f"--rootfs={rootfs}")
    proot_args.append(f"--cwd={login_wd}")

    # Core file system bindings.
    proot_args += ["--bind=/dev", "--bind=/proc", "--bind=/sys"]

    if dist_type != "termux":
        proot_args += [
            "--bind=/dev/urandom:/dev/random",
            "--bind=/proc/self/fd:/dev/fd",
        ]
        for i, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
            if os.path.exists(f"/proc/self/fd/{i}"):
                proot_args.append(f"--bind=/proc/self/fd/{i}:/dev/{name}")

        proot_args.append(f"--bind={rootfs}/sys/.empty:/sys/fs/selinux")
        proot_args += fake_proc_bindings(dist_name)

        tmp_dir = os.path.join(rootfs, "tmp")
        os.makedirs(tmp_dir, exist_ok=True)
        try:
            os.chmod(tmp_dir, 0o1777)
        except OSError:
            pass
        proot_args.append(f"--bind={tmp_dir}:/dev/shm")

    # Non-isolated host bindings.
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
            proot_args.append(f"--bind=/data/data/{TERMUX_APP_PACKAGE}/cache")
            proot_args.append(f"--bind={TERMUX_HOME}")
        else:
            os.makedirs(
                os.path.join(rootfs, "data", "data", "com.termux", "cache"),
                exist_ok=True,
            )

        proot_args += _storage_bindings()

    # System / emulator bindings — always enabled for termux distros.
    if dist_type == "termux" or not isolated or need_emu:
        proot_args += _system_bindings()
        if dist_type != "termux":
            proot_args.append(f"--bind={PREFIX}")

    # --termux-home: mount Termux home into the distro.
    if use_termux_home:
        if dist_type == "termux":
            proot_args.append(f"--bind={TERMUX_HOME}:{_TERMUX_HOME_INNER}")
        elif login_user == "root":
            proot_args.append(f"--bind={TERMUX_HOME}:/root")
        else:
            proot_args.append(f"--bind={TERMUX_HOME}:{login_home}")

    # --shared-tmp (not applicable to termux distros).
    if shared_tmp and dist_type != "termux":
        proot_args.append(f"--bind={PREFIX}/tmp:/tmp")

    # Custom bindings.
    for bnd in custom_binds:
        if ":" in bnd:
            src, dst = bnd.split(":", 1)
        else:
            src, dst = bnd, None

        src = os.path.abspath(src)

        if dst in (".", ".."):
            msg()
            msg(f"{C['BRED']}Error: '.' and '..' are not allowed as binding destination.{C['RST']}")
            msg()
            sys.exit(1)

        proot_args.append(f"--bind={src}:{dst}" if dst else f"--bind={src}")

    if redirect_ports:
        proot_args.append("-p")

    proot_args += inner

    env = os.environ.copy()
    env.pop("LD_PRELOAD", None)
    if dist_type != "termux" and os.path.isdir(os.path.join(rootfs, ".l2s")):
        env["PROOT_L2S_DIR"] = os.path.join(rootfs, ".l2s")

    os.execvpe("proot", proot_args, env)
