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
# rather than the way the host resolves a path. _open_guest_file() walks
# the components off a descriptor on the rootfs: an existing symlink is
# followed, because a legitimate image may well ship one (Nix points
# /etc/passwd at an absolute store path), but its target is re-anchored at
# the rootfs and ".." can never climb above it. Naming the file directly,
# as this used to, let an image point /etc — or /etc/passwd itself — at a
# host file and have the build read it.

import os
import stat

from proot_distro import dirfd

# Same budget paths._resolve_within_root and tar_extract._safe_resolve use.
_MAX_SYMLINK_HOPS = 40

# Nothing bounds how large an image's passwd or group file is, and the
# whole point of reading it is that it was not written by us. A megabyte
# is thousands of entries; past that the file is not a passwd file, and
# reading it — or one arbitrarily long line of it — into memory is how a
# build gets killed rather than failed.
_MAX_ID_FILE_BYTES = 1 << 20


def _open_guest_file(rootfs_dir, guest_path):
    """Open the absolute guest path *guest_path* under *rootfs_dir*.

    Returns a text-mode file object for a regular file, or None — the path
    does not exist, names something other than a regular file, or leads
    out of the rootfs. Undecodable bytes are replaced rather than raised:
    the content is the image's, and a UnicodeDecodeError is not an
    OSError, so no caller's net would have caught one.

    Components are consumed one at a time off a directory descriptor, so
    the resolve and the open are a single walk with no window between
    them, and every hop is clamped: an absolute symlink target restarts at
    the rootfs (the guest's "/"), a relative one continues from the
    directory holding the link, and ".." stops at the rootfs the way a
    chroot does.
    """
    try:
        root_fd = dirfd.opendir(rootfs_dir)
    except OSError:
        return None
    # One fd per level of the current path; ".." pops rather than opening
    # a name that would climb out of the rootfs.
    stack = [root_fd]
    pending = guest_path.split("/")
    hops = 0
    try:
        while pending:
            part = pending.pop(0)
            if part in ("", os.curdir):
                continue
            if part == os.pardir:
                if len(stack) > 1:
                    os.close(stack.pop())
                continue
            cur = stack[-1]
            try:
                st = dirfd.lstat_at(cur, part)
            except OSError:
                return None
            if stat.S_ISLNK(st.st_mode):
                hops += 1
                if hops > _MAX_SYMLINK_HOPS:
                    return None
                try:
                    target = os.readlink(part, dir_fd=cur)
                except OSError:
                    return None
                if target.startswith("/"):
                    while len(stack) > 1:
                        os.close(stack.pop())
                pending[:0] = target.split("/")
                continue
            if stat.S_ISDIR(st.st_mode):
                try:
                    stack.append(dirfd.opendir_at(cur, part))
                except OSError:
                    return None
                continue
            if any(p not in ("", os.curdir) for p in pending):
                return None     # a non-directory in the middle of the path
            try:
                fd, _st = dirfd.open_regular_at(cur, part, os.O_RDONLY)
            except OSError:
                return None
            return open(fd, encoding="utf-8", errors="replace")
        return None             # the path named a directory, not a file
    finally:
        for fd in stack:
            try:
                os.close(fd)
            except OSError:
                pass


def _read_capped(fh):
    """Read at most _MAX_ID_FILE_BYTES, dropping a line the cap cut in half."""
    data = fh.read(_MAX_ID_FILE_BYTES + 1)
    if len(data) <= _MAX_ID_FILE_BYTES:
        return data
    data = data[:_MAX_ID_FILE_BYTES]
    return data[:data.rfind("\n") + 1]


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
    fh = _open_guest_file(
        rootfs_dir, "/etc/group" if is_group else "/etc/passwd",
    )
    if fh is None:
        return default
    try:
        with fh:
            data = _read_capped(fh)
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
