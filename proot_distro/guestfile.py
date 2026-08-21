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

# Architecture: reading a file out of a container the way the *guest*
# would see it, rather than the way the host resolves a path.
#
# Two places need this and both read the same kind of file: `login`, which
# takes a user's uid/gid/home/shell out of the container's /etc/passwd and
# /etc/group before it exec's proot, and the build engine, which resolves
# USER and COPY --chown against the stage rootfs's own copies. Everything
# involved is image or guest content — the file, and every directory
# component leading to it — so the rule is a chroot's: an existing symlink
# is followed, because a legitimate image ships one (Nix points
# /etc/passwd at an absolute store path), but an absolute target restarts
# at the rootfs, a relative one continues from the directory holding the
# link, and ".." stops at the rootfs.
#
# The walk that resolves is the walk that opens: components are consumed
# one at a time off a directory descriptor, so there is no window between
# deciding what the path means and reading it. Composing a host path and
# opening it afterwards was wrong twice over. It let a *middle* component
# leave the rootfs for good — the host kernel resolves `<rootfs>/etc` for
# a name like `<rootfs>/etc/passwd`, so an image (or a guest, between
# sessions) shipping `etc -> /etc` had login read the host's passwd file
# and hand a host user's uid, home and shell to the session. And even
# with every component checked, a container the caller holds only a shared
# lock on can swap one between the check and the open, which is not a
# corner case for `login`: another session of the same container may well
# be running.
#
# open_regular_at() refuses a FIFO planted under the name, which used to
# block the command for as long as no peer turned up, and the read is
# capped: nothing bounds how large an image makes the file, or one line
# of it.

import os
import stat

from proot_distro import dirfd
from proot_distro.l2s import resolve_l2s_target

# Same budget paths._resolve_within_root and tar_extract._safe_resolve use.
_MAX_SYMLINK_HOPS = 40

# How many path components one lookup may consume in total. Forty hops of
# an image's own symlinks can name an arbitrary number of them between
# them, and each is a step this walk takes; no real path is anywhere near
# this, and a crafted chain stops here rather than running as long as the
# image likes.
_MAX_PATH_COMPONENTS = 4096

# Nothing bounds how large an image's passwd or group file is, and the
# whole point of reading it is that it was not written by us. A megabyte
# is thousands of entries; past that the file is not a passwd file, and
# reading it — or one arbitrarily long line of it — into memory is how a
# command gets killed rather than failed.
MAX_ID_FILE_BYTES = 1 << 20


def _l2s_parts(rootfs_dir, levels, name, target):
    """Components of the file an l2s stand-in symlink really names, or None.

    proot's --link2symlink extension replaces a hard link with a symlink
    whose target is a *host*-absolute path into the backing store, so the
    chroot rule above — restart an absolute target at the rootfs — is
    exactly wrong for one: it would send the walk to
    <rootfs>/<rootfs>/.l2s/… and lose the file. resolve_l2s_target()
    recognises one by basename, follows the whole chain and answers only
    for a target that really does land inside the rootfs; what comes back
    is re-walked from the rootfs descriptor like any other path, so the
    lexical answer decides *where* to look and never how to get there.
    """
    rootfs_real = os.path.realpath(rootfs_dir)
    link_path = os.path.join(rootfs_real, *levels, name)
    resolved = resolve_l2s_target(link_path, target, rootfs_real)
    if resolved is None:
        return None
    rel = os.path.relpath(resolved, rootfs_real)
    parts = [p for p in rel.split(os.sep) if p and p != os.curdir]
    if os.pardir in parts:
        return None
    return parts


def _dir_key(fd):
    """(device, inode) of the directory *fd* refers to, or None."""
    try:
        st = os.fstat(fd)
    except OSError:
        return None
    return (st.st_dev, st.st_ino)


def _resolve(rootfs_dir, guest_path, root_fd=None):
    """Walk to the entry *guest_path* names. (stack, name, st), or None.

    stack[-1] is a descriptor on the directory holding *name*, *st* is
    that entry's lstat and is never a symlink's — the walk follows the
    final component too, so what comes back is the entry a caller would
    really read. The caller owns every descriptor in *stack* and closes
    them all.

    None covers every way the path does not name an entry inside the
    rootfs: a missing or unreadable component, a non-directory in the
    middle of the path, a symlink chain past the hop budget, a path with
    more components than any real one has, or a path that ends on a
    directory.

    Components are consumed one at a time off a directory descriptor, so
    the resolve and whatever the caller does with the result share one
    walk with no name resolved twice, and every hop is clamped: an
    absolute symlink target restarts at the rootfs (the guest's "/"), a
    relative one continues from the directory holding the link, and ".."
    stops at the rootfs the way a chroot does. An l2s stand-in is the one
    target not re-anchored — see _l2s_parts.

    Two descriptors are open at a time however deep the path goes: the
    rootfs and the level being looked at. Keeping one per level would
    make how many a *symlink target* can name — which is the image's
    choice, and unbounded — decide how many this process holds, so ".."
    reopens the level above through the current one instead. That is the
    only step where a descriptor is derived from something other than a
    name under a directory this walk already validated, so the level it
    lands on is checked against the (device, inode) recorded on the way
    down: a directory a guest moves elsewhere mid-walk has a different
    parent, and following it would leave the rootfs.

    *root_fd* is the rootfs when the caller has already pinned it, and a
    caller that has one must pass it: `login` opens containers/<name>/
    rootfs with an O_NOFOLLOW walk and then reads the guest's passwd,
    group and shell out of it, and opening `rootfs_dir` by name here
    resolved `containers/<name>` a second time -- guest-writable on
    Termux, with only a shared lock held. A private duplicate is taken
    so the ownership below stays this function's either way. *rootfs_dir*
    is still needed as a string: _l2s_parts recognises proot's hard-link
    stand-ins by the path they name.
    """
    try:
        root_fd = (dirfd.reopen(root_fd) if root_fd is not None
                   else dirfd.opendir(rootfs_dir))
    except OSError:
        return None
    root_key = _dir_key(root_fd)
    if root_key is None:
        os.close(root_fd)
        return None
    # (name, (dev, ino)) per level below the rootfs. Names only for an
    # l2s link's own position; the keys are what ".." is checked against.
    trail = []
    cur, owned = root_fd, False
    pending = guest_path.split("/")
    hops = 0
    steps = 0
    result = None
    try:
        while pending:
            part = pending.pop(0)
            steps += 1
            if steps > _MAX_PATH_COMPONENTS:
                return None
            if part in ("", os.curdir):
                continue
            if part == os.pardir:
                if not trail:
                    continue        # already at the guest's "/"
                want = trail[-2][1] if len(trail) > 1 else root_key
                try:
                    up = dirfd.opendir_at(cur, os.pardir)
                except OSError:
                    return None
                if _dir_key(up) != want:
                    os.close(up)   # not the level this walk came down from
                    return None
                if owned:
                    os.close(cur)
                trail.pop()
                if trail:
                    cur, owned = up, True
                else:
                    os.close(up)   # back at the rootfs, which is still open
                    cur, owned = root_fd, False
                continue
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
                names = [level for level, _key in trail]
                backing = _l2s_parts(rootfs_dir, names, part, target)
                if backing is None and not target.startswith("/"):
                    # Relative: carry on from the directory the link sits in.
                    pending[:0] = target.split("/")
                    continue
                pending[:0] = backing if backing is not None \
                    else target.split("/")
                # Absolute, or an l2s stand-in resolved back to a path
                # under the rootfs: either way the walk starts again at
                # the guest's "/".
                if owned:
                    os.close(cur)
                cur, owned = root_fd, False
                del trail[:]
                continue
            if stat.S_ISDIR(st.st_mode):
                try:
                    nxt = dirfd.opendir_at(cur, part)
                except OSError:
                    return None
                key = _dir_key(nxt)
                if key is None:
                    os.close(nxt)
                    return None
                if owned:
                    os.close(cur)
                cur, owned = nxt, True
                trail.append((part, key))
                continue
            if any(p not in ("", os.curdir) for p in pending):
                return None     # a non-directory in the middle of the path
            result = ([root_fd, cur] if owned else [root_fd], part, st)
            return result
        return None             # the path named a directory, not a file
    finally:
        if result is None:
            if owned:
                try:
                    os.close(cur)
                except OSError:
                    pass
            try:
                os.close(root_fd)
            except OSError:
                pass


def _close_stack(stack):
    for fd in stack:
        try:
            os.close(fd)
        except OSError:
            pass


def open_guest_file(rootfs_dir, guest_path, *, root_fd=None):
    """Open the absolute guest path *guest_path* under *rootfs_dir*.

    Returns a text-mode file object for a regular file, or None — the path
    does not exist, names something other than a regular file, or leads
    out of the rootfs. Undecodable bytes are replaced rather than raised:
    the content is the image's, and a UnicodeDecodeError is not an
    OSError, so no caller's net would have caught one.

    The entry is opened as (directory fd, name) off the walk that
    resolved it, and through open_regular_at(), which refuses a FIFO
    planted under the name since the lstat rather than blocking on a peer
    that never comes.
    """
    found = _resolve(rootfs_dir, guest_path, root_fd)
    if found is None:
        return None
    stack, name, st = found
    try:
        if not stat.S_ISREG(st.st_mode):
            return None
        try:
            fd, _st = dirfd.open_regular_at(stack[-1], name, os.O_RDONLY)
        except OSError:
            return None
        return open(fd, encoding="utf-8", errors="replace")
    finally:
        _close_stack(stack)


def read_capped(fh):
    """Read at most MAX_ID_FILE_BYTES, dropping a line the cap cut in half."""
    data = fh.read(MAX_ID_FILE_BYTES + 1)
    if len(data) <= MAX_ID_FILE_BYTES:
        return data
    data = data[:MAX_ID_FILE_BYTES]
    return data[:data.rfind("\n") + 1]


def read_guest_file(rootfs_dir, guest_path, *, root_fd=None):
    """Content of *guest_path* under *rootfs_dir*, capped. None if unreadable.

    None covers every way the file is not there to be read — missing,
    refused, not a regular file — because every caller answers the same
    way to all of them: fall back to what it would do without the file.
    """
    fh = open_guest_file(rootfs_dir, guest_path, root_fd=root_fd)
    if fh is None:
        return None
    try:
        with fh:
            return read_capped(fh)
    except OSError:
        return None


def guest_file_exists(rootfs_dir, guest_path, *, root_fd=None) -> bool:
    """True when *guest_path* resolves to a regular file inside the rootfs.

    The same walk, for a caller that only asks whether the file is there
    — `login` deciding whether the container has an /etc/passwd at all,
    or whether the shell it is about to run exists. os.path.isfile() on a
    composed path answered that question about a host file whenever a
    component left the rootfs.

    The answer comes from the walk's own lstat, so nothing is opened: a
    shell the image ships execute-only still counts as there, and neither
    a FIFO nor a device planted under the name is touched.
    """
    found = _resolve(rootfs_dir, guest_path, root_fd)
    if found is None:
        return False
    stack, _name, st = found
    try:
        return stat.S_ISREG(st.st_mode)
    finally:
        _close_stack(stack)


__all__ = (
    "MAX_ID_FILE_BYTES",
    "guest_file_exists",
    "open_guest_file",
    "read_capped",
    "read_guest_file",
)
