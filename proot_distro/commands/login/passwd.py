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

# Architecture: passwd/group lookups against the container's own files,
# plus the path-resolution helper that follows guest symlinks within
# the rootfs namespace. Absolute symlink targets are re-rooted under
# *rootfs* so images like Nix that point /etc/passwd at an absolute
# store path inside the guest still resolve correctly.

import errno
import os
import stat


def resolve_rootfs_path(rootfs: str, guest_path: str) -> str:
    """Resolve an absolute guest path to its real host path.

    Follows symlinks within the rootfs namespace, at most 40 hops
    before raising OSError(ELOOP). Absolute targets are re-rooted
    under *rootfs* via os.path.normpath, which both prevents .. escape
    and handles Nix-style images where /etc/passwd is a symlink to an
    absolute store path that only exists inside the guest.
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


def read_passwd_field(rootfs: str, user: str, field_index: int) -> str:
    """Return a single colon-delimited field for *user* from /etc/passwd."""
    try:
        passwd = resolve_rootfs_path(rootfs, "/etc/passwd")
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


def find_passwd_by_uid(rootfs: str, uid: str) -> tuple:
    """Return (home, shell, primary_gid) for the given UID, or ('','','')."""
    try:
        passwd = resolve_rootfs_path(rootfs, "/etc/passwd")
    except OSError:
        return ("", "", "")
    try:
        with open(passwd) as fh:
            for line in fh:
                parts = line.strip().split(":")
                if len(parts) >= 7 and parts[2] == uid:
                    return (parts[5], parts[6], parts[3])
    except OSError:
        pass
    return ("", "", "")


def read_group_gid(rootfs: str, group: str) -> str:
    """Return the GID string for the named group from /etc/group, or ''."""
    try:
        group_file = resolve_rootfs_path(rootfs, "/etc/group")
    except OSError:
        return ""
    try:
        with open(group_file) as fh:
            for line in fh:
                parts = line.strip().split(":")
                if parts and parts[0] == group and len(parts) > 2:
                    return parts[2]
    except OSError:
        pass
    return ""
