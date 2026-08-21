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

# Architecture: Resolve user / group names against the rootfs's own
# /etc/passwd and /etc/group. Used by COPY --chown=… and by the proot
# invocation that runs each RUN step.
#
# Both files are image content, and so is every directory component
# leading to them, so the lookup is done the way the guest would see it
# rather than the way the host resolves a path. That walk is
# guestfile.open_guest_file(), shared with `login`, which reads the same
# two files for the same reason: see there for what naming them instead
# used to allow.

from proot_distro.guestfile import open_guest_file, read_capped


def resolve_id(rootfs_dir, name, is_group, default, *, root_fd=None):
    """Translate a user or group name into a numeric ID.

    Numeric strings pass through. Otherwise the name is looked up in
    the rootfs's own /etc/passwd or /etc/group (not the host's). Falls
    back to *default* on missing files or unknown names.

    *root_fd* is the rootfs when the caller has pinned it, and a caller
    that has one must pass it: what comes back is the uid and gid proot
    runs the step as, and starting the walk at a name would let the
    directory that name points to decide them.
    """
    if not name:
        return default
    if name.isdigit():
        return int(name)
    fh = open_guest_file(
        rootfs_dir, "/etc/group" if is_group else "/etc/passwd",
        root_fd=root_fd,
    )
    if fh is None:
        return default
    try:
        with fh:
            data = read_capped(fh)
    except OSError:
        return default
    for line in data.splitlines():
        parts = line.split(":")
        if parts and parts[0] == name and len(parts) > 2:
            try:
                return int(parts[2])
            except ValueError:
                return default
    return default


def resolve_chown(rootfs_dir, chown, *, root_fd=None):
    """Resolve --chown=user[:group] against the rootfs /etc/passwd."""
    if ":" in chown:
        user, group = chown.split(":", 1)
    else:
        user, group = chown, ""
    uid = resolve_id(rootfs_dir, user, is_group=False, default=0,
                     root_fd=root_fd)
    gid = (
        resolve_id(rootfs_dir, group, is_group=True, default=uid,
                   root_fd=root_fd)
        if group else uid
    )
    return uid, gid


def resolve_user_for_proot(rootfs_dir, user_spec, *, root_fd=None):
    """Resolve a USER directive's value into a (uid, gid) pair."""
    if not user_spec:
        return (0, 0)
    spec = str(user_spec).strip()
    if ":" in spec:
        u, g = spec.split(":", 1)
    else:
        u, g = spec, ""
    uid = resolve_id(rootfs_dir, u, is_group=False, default=0,
                     root_fd=root_fd)
    gid = (
        resolve_id(rootfs_dir, g, is_group=True, default=uid,
                   root_fd=root_fd) if g else uid
    )
    return uid, gid
