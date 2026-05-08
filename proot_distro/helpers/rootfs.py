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

# Architecture: Post-extraction rootfs fixup helpers. Each function targets a
# single aspect of Termux/Android integration (DNS, PATH, Android UIDs, etc.).
# Kept separate from the install command so the same fixups can be applied by
# other entry points (e.g. restore). No subprocess calls here — only Python
# standard-library filesystem operations.

import grp
import os
import pwd
import stat

from proot_distro.constants import (
    DEFAULT_PRIMARY_NS,
    DEFAULT_SECONDARY_NS,
)


def write_resolv_conf(rootfs: str) -> None:
    """Replace /etc/resolv.conf with a plain file containing default DNS servers."""
    path = os.path.join(rootfs, "etc", "resolv.conf")
    try:
        os.remove(path)
    except OSError:
        pass
    with open(path, "w") as fh:
        fh.write(f"nameserver {DEFAULT_PRIMARY_NS}\n")
        fh.write(f"nameserver {DEFAULT_SECONDARY_NS}\n")


def write_hosts(rootfs: str) -> None:
    """Write a minimal /etc/hosts into the rootfs."""
    path = os.path.join(rootfs, "etc", "hosts")
    try:
        os.chmod(
            path,
            stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
        )
    except OSError:
        pass
    with open(path, "w") as fh:
        fh.write(
            "# IPv4.\n"
            "127.0.0.1   localhost.localdomain localhost\n\n"
            "# IPv6.\n"
            "::1         localhost.localdomain localhost"
            " ip6-localhost ip6-loopback\n"
            "fe00::0     ip6-localnet\n"
            "ff00::0     ip6-mcastprefix\n"
            "ff02::1     ip6-allnodes\n"
            "ff02::2     ip6-allrouters\n"
            "ff02::3     ip6-allhosts\n"
        )


def register_android_ids(rootfs: str) -> None:
    """Add the Termux Android UID/GID entries to passwd/shadow/group/gshadow."""
    for p in ("etc/passwd", "etc/shadow", "etc/group", "etc/gshadow"):
        full = os.path.join(rootfs, p)
        if os.path.exists(full):
            try:
                os.chmod(
                    full,
                    stat.S_IRUSR | stat.S_IWUSR | stat.S_IRGRP | stat.S_IROTH,
                )
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
    group_path = os.path.join(rootfs, "etc", "group")
    gshadow_path = os.path.join(rootfs, "etc", "gshadow")

    try:
        with open(passwd_path, "a") as fh:
            fh.write(
                f"aid_{username_result}:x:{uid}:{gid}:Termux:/:/sbin/nologin\n"
            )
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
                fh.write(
                    f"aid_{gname}:x:{g}:root,aid_{username_result}\n"
                )
            if os.path.exists(gshadow_path):
                with open(gshadow_path, "a") as fh:
                    fh.write(
                        f"aid_{gname}:*::root,aid_{username_result}\n"
                    )
        except OSError:
            pass
