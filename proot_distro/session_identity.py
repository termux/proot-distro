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

# Architecture: passwd/group lookups against the container's own files.
#
# The files and every directory component leading to them are guest
# content, so the lookup goes through guestfile.open_guest_file(), the
# same clamped, fd-borne, capped walk the build engine resolves USER and
# COPY --chown with. This module used to compose `<rootfs><guest path>`
# and hand the string to open(): the host kernel then resolved the middle
# components, so an image (or a guest between sessions) shipping
# `etc -> /etc` had `login` read the *host's* passwd file and take a host
# user's uid, gid, home and shell from it — and `login` holds only a
# shared lock, so even with every component checked a live session of the
# same container could swap one between the check and the open. A FIFO
# under either name blocked the login for as long as no peer turned up,
# and a file with no newline in it was read into memory whole.
#
# Absolute symlink targets are re-rooted under *rootfs* so images like Nix
# that point /etc/passwd at an absolute store path still resolve, and an
# l2s stand-in — proot's hard-link replacement, whose target is a host
# path into <rootfs>/.l2s — is followed to the file holding the content
# instead. Both are the shared walk's doing; see guestfile.
#
# Every entry point takes *root_fd*, the descriptor `login` pinned the
# rootfs as, and passes it down: the walk is only as good as where it
# starts, and starting it at `containers/<name>/rootfs` resolved that
# name again — after the check, with a shared lock held, on a directory
# that is guest-writable on Termux.

from proot_distro.guestfile import guest_file_exists, read_guest_file


def _entries(rootfs: str, guest_path: str, root_fd=None):
    """Yield the colon-split fields of each line of a passwd-shaped file."""
    data = read_guest_file(rootfs, guest_path, root_fd=root_fd)
    if data is None:
        return
    for line in data.splitlines():
        yield line.strip().split(":")


def passwd_available(rootfs: str, *, root_fd=None) -> bool:
    """True when the container has an /etc/passwd to look users up in."""
    return guest_file_exists(rootfs, "/etc/passwd", root_fd=root_fd)


def shell_available(rootfs: str, guest_path: str, *, root_fd=None) -> bool:
    """True when *guest_path* is a regular file inside the container."""
    return guest_file_exists(rootfs, guest_path, root_fd=root_fd)


def read_passwd_entry(rootfs: str, user: str, *, root_fd=None) -> list:
    """Return the fields of *user*'s /etc/passwd line, or [] if absent.

    One read for the whole entry: the fields are wanted together, and
    re-opening the file per field gave four chances for it to change
    underneath the login instead of one.
    """
    for parts in _entries(rootfs, "/etc/passwd", root_fd):
        if parts and parts[0] == user:
            return parts
    return []


def passwd_field(parts: list, field_index: int) -> str:
    """One field of a /etc/passwd entry, or '' when the line is short."""
    if len(parts) > field_index:
        return parts[field_index]
    return ""


def find_passwd_by_uid(rootfs: str, uid: str, *, root_fd=None) -> tuple:
    """Return (home, shell, primary_gid) for the given UID, or ('','','')."""
    for parts in _entries(rootfs, "/etc/passwd", root_fd):
        if len(parts) >= 7 and parts[2] == uid:
            return (parts[5], parts[6], parts[3])
    return ("", "", "")


def read_group_gid(rootfs: str, group: str, *, root_fd=None) -> str:
    """Return the GID string for the named group from /etc/group, or ''."""
    for parts in _entries(rootfs, "/etc/group", root_fd):
        if parts and parts[0] == group and len(parts) > 2:
            return parts[2]
    return ""


__all__ = (
    "find_passwd_by_uid",
    "passwd_available",
    "passwd_field",
    "read_group_gid",
    "read_passwd_entry",
    "shell_available",
)
