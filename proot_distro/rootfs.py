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
import grp
import os
import pwd
import re
import stat
import subprocess

from proot_distro.constants import (
    INSTALLED_ROOTFS_DIR,
    PREFIX,
    DEFAULT_PATH_ENV,
    DEFAULT_PRIMARY_NS,
    DEFAULT_SECONDARY_NS,
    DEFAULT_FAKE_KERNEL_RELEASE,
    _SAVED_LD_PRELOAD,
)
from proot_distro.colors import C, msg
from proot_distro.arch import get_device_cpu_arch, get_emulator_args
from proot_distro.sysdata import setup_fake_sysdata


def _write_environment(rootfs: str) -> None:
    env_path = os.path.join(rootfs, "etc", "environment")
    try:
        os.chmod(env_path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass

    lines = []
    for var in ("ANDROID_ART_ROOT", "ANDROID_DATA", "ANDROID_I18N_ROOT",
                "ANDROID_ROOT", "ANDROID_RUNTIME_ROOT", "ANDROID_TZDATA_ROOT",
                "BOOTCLASSPATH", "COLORTERM", "DEX2OATBOOTCLASSPATH",
                "EXTERNAL_STORAGE"):
        val = os.environ.get(var, "")
        if val:
            lines.append(f"{var}={val}\n")

    lines += [
        "LANG=en_US.UTF-8\n",
        "MOZ_FAKE_NO_SANDBOX=1\n",
        f"PATH={DEFAULT_PATH_ENV}\n",
        "PULSE_SERVER=127.0.0.1\n",
        f"TERM={os.environ.get('TERM', 'xterm-256color')}\n",
        "TMPDIR=/tmp\n",
    ]

    os.makedirs(os.path.dirname(env_path), exist_ok=True)
    with open(env_path, "a") as fh:
        fh.writelines(lines)


def _fix_path_in_configs(rootfs: str) -> None:
    for rel in ("/etc/bash.bashrc", "/etc/profile", "/etc/login.defs"):
        path = rootfs + rel
        if not os.path.exists(path):
            continue
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}Updating PATH in '{path}'...{C['RST']}")
        try:
            with open(path) as fh:
                content = fh.read()
            new = re.sub(
                r'\b(PATH=)("?[^"\s]+("|\Z|\b))',
                f'\\1"{DEFAULT_PATH_ENV}"',
                content,
            )
            if new != content:
                with open(path, "w") as fh:
                    fh.write(new)
        except OSError:
            pass


def _write_resolv_conf(rootfs: str) -> None:
    path = os.path.join(rootfs, "etc", "resolv.conf")
    try:
        os.remove(path)
    except OSError:
        pass
    with open(path, "w") as fh:
        fh.write(f"nameserver {DEFAULT_PRIMARY_NS}\n")
        fh.write(f"nameserver {DEFAULT_SECONDARY_NS}\n")


def _write_hosts(rootfs: str) -> None:
    path = os.path.join(rootfs, "etc", "hosts")
    try:
        os.chmod(path, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
    except OSError:
        pass
    with open(path, "w") as fh:
        fh.write(
            "# IPv4.\n"
            "127.0.0.1   localhost.localdomain localhost\n\n"
            "# IPv6.\n"
            "::1         localhost.localdomain localhost ip6-localhost ip6-loopback\n"
            "fe00::0     ip6-localnet\n"
            "ff00::0     ip6-mcastprefix\n"
            "ff02::1     ip6-allnodes\n"
            "ff02::2     ip6-allrouters\n"
            "ff02::3     ip6-allhosts\n"
        )


def _register_android_ids(rootfs: str) -> None:
    """Add the Termux Android UID/GID entries to passwd/shadow/group/gshadow."""
    for p in ("etc/passwd", "etc/shadow", "etc/group", "etc/gshadow"):
        full = os.path.join(rootfs, p)
        if os.path.exists(full):
            try:
                os.chmod(full, stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH)
            except OSError:
                pass

    try:
        uid = os.getuid()
        gid = os.getgid()
        username_result = pwd.getpwuid(uid).pw_name
    except Exception:
        return

    passwd_path = os.path.join(rootfs, "etc", "passwd")
    shadow_path = os.path.join(rootfs, "etc", "shadow")
    group_path  = os.path.join(rootfs, "etc", "group")
    gshadow_path = os.path.join(rootfs, "etc", "gshadow")

    try:
        with open(passwd_path, "a") as fh:
            fh.write(f"aid_{username_result}:x:{uid}:{gid}:Termux:/:/sbin/nologin\n")
        with open(shadow_path, "a") as fh:
            fh.write(f"aid_{username_result}:*:18446:0:99999:7:::\n")
    except OSError:
        pass

    seen: set[int] = set()
    all_gids: list[int] = []
    for g in [gid] + os.getgroups():
        if g not in seen:
            seen.add(g)
            all_gids.append(g)

    for g in all_gids:
        try:
            gname = grp.getgrgid(g).gr_name
        except KeyError:
            continue
        try:
            with open(group_path, "a") as fh:
                fh.write(f"aid_{gname}:x:{g}:root,aid_{username_result}\n")
            if os.path.exists(gshadow_path):
                with open(gshadow_path, "a") as fh:
                    fh.write(f"aid_{gname}:*::root,aid_{username_result}\n")
        except OSError:
            pass


def _run_post_install(dist_name: str, script: str, dist_arch: str) -> None:
    """Execute post_install_automation inside proot."""
    if not script.strip():
        return

    rootfs = os.path.join(INSTALLED_ROOTFS_DIR, dist_name)
    setup_fake_sysdata(dist_name)

    script_path_host = os.path.join(rootfs, "tmp", "_post_install.sh")
    os.makedirs(os.path.join(rootfs, "tmp"), exist_ok=True)
    with open(script_path_host, "w") as fh:
        fh.write("#!/bin/sh\nset -e\n")
        fh.write(script)
    os.chmod(script_path_host, 0o755)

    device_arch = get_device_cpu_arch()
    emu_args = get_emulator_args(dist_arch, device_arch)

    cmd = (
        ["proot"]
        + emu_args
        + [
            "-L",
            f"--kernel-release={DEFAULT_FAKE_KERNEL_RELEASE}",
            "--link2symlink",
            "--kill-on-exit",
            f"--rootfs={rootfs}",
            "--root-id",
            "--cwd=/root",
            "--bind=/dev",
            "--bind=/dev/urandom:/dev/random",
            "--bind=/proc",
            "--bind=/proc/self/fd:/dev/fd",
            "--bind=/proc/self/fd/0:/dev/stdin",
            "--bind=/proc/self/fd/1:/dev/stdout",
            "--bind=/proc/self/fd/2:/dev/stderr",
            "--bind=/sys",
        ]
        + [f"--bind={rootfs}/proc/.loadavg:/proc/loadavg",
           f"--bind={rootfs}/proc/.stat:/proc/stat",
           f"--bind={rootfs}/proc/.uptime:/proc/uptime",
           f"--bind={rootfs}/proc/.version:/proc/version",
           f"--bind={rootfs}/proc/.vmstat:/proc/vmstat",
           f"--bind={rootfs}/proc/.sysctl_entry_cap_last_cap:/proc/sys/kernel/cap_last_cap",
           f"--bind={rootfs}/proc/.sysctl_inotify_max_user_watches:/proc/sys/fs/inotify/max_user_watches",
           f"--bind={rootfs}/sys/.empty:/sys/fs/selinux"]
        + [
            "/usr/bin/env", "-i",
            "HOME=/root",
            "LANG=C.UTF-8",
            "PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin",
            f"TERM={os.environ.get('TERM', 'xterm-256color')}",
            "TMPDIR=/tmp",
            "/bin/sh", "/tmp/_post_install.sh",
        ]
    )

    env = os.environ.copy()
    env.pop("LD_PRELOAD", None)
    env["PROOT_L2S_DIR"] = os.path.join(rootfs, ".l2s")

    try:
        subprocess.run(cmd, env=env, check=True)
    finally:
        try:
            os.remove(script_path_host)
        except OSError:
            pass

    if _SAVED_LD_PRELOAD:
        os.environ["LD_PRELOAD"] = _SAVED_LD_PRELOAD
