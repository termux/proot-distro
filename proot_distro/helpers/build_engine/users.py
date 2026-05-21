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

import os


def resolve_id(rootfs_dir, name, is_group, default):
    """Translate a user or group name into a numeric ID.

    Numeric strings pass through. Otherwise the name is looked up in
    the rootfs's own /etc/passwd or /etc/group (not the host's). Falls
    back to *default* on missing files or unknown names.
    """
    if not name:
        return default
    if name.isdigit():
        return int(name)
    path = os.path.join(
        rootfs_dir, "etc", "group" if is_group else "passwd",
    )
    try:
        with open(path) as fh:
            for line in fh:
                parts = line.split(":")
                if parts and parts[0] == name and len(parts) > 2:
                    try:
                        return int(parts[2])
                    except ValueError:
                        return default
    except OSError:
        pass
    return default


def resolve_chown(rootfs_dir, chown):
    """Resolve --chown=user[:group] against the rootfs /etc/passwd."""
    if ":" in chown:
        user, group = chown.split(":", 1)
    else:
        user, group = chown, ""
    uid = resolve_id(rootfs_dir, user, is_group=False, default=0)
    gid = (
        resolve_id(rootfs_dir, group, is_group=True, default=uid)
        if group else uid
    )
    return uid, gid


def resolve_user_for_proot(rootfs_dir, user_spec):
    """Resolve a USER directive's value into a (uid, gid) pair."""
    if not user_spec:
        return (0, 0)
    spec = str(user_spec).strip()
    if ":" in spec:
        u, g = spec.split(":", 1)
    else:
        u, g = spec, ""
    uid = resolve_id(rootfs_dir, u, is_group=False, default=0)
    gid = (
        resolve_id(rootfs_dir, g, is_group=True, default=uid) if g else uid
    )
    return uid, gid
