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

# Architecture: openat(2)-based filesystem walking for `copy` and `sync`.
#
# Every helper here names a file as (directory fd, entry name) instead of
# as a path, and opens directories with O_NOFOLLOW. Path-based walking
# cannot be made safe against a hostile container: a path is resolved
# once and used later, and in between a process inside the container can
# replace a component with a symlink so the operation lands on the host.
# paths.pin_path() closes that window for the two endpoints; carrying fds
# closes it for everything below them, because an fd refers to an inode
# rather than to a name, and a descent that meets a symlink fails instead
# of following it.
#
# Note that Linux reports O_NOFOLLOW|O_DIRECTORY on a symlink as ENOTDIR,
# not ELOOP, so REFUSED covers both.
#
# Two rules here are easy to break by accident, because in both cases the
# obvious call looks fd-based but is not proof against a planted entry:
#
#   - chmod. Linux has no AT_SYMLINK_NOFOLLOW for fchmodat(2), so naming an
#     entry in os.chmod() hands the mode change to whatever a symlink
#     planted since the lstat points at — a host file. Every chmod below
#     therefore goes through _chmod_fd() on a descriptor that an
#     O_NOFOLLOW open has already validated.
#   - O_NOFOLLOW refuses a symlink but says nothing about a named pipe, and
#     opening one waits for a peer that a hostile container never has to
#     provide. Regular-file endpoints are opened with open_regular_at(),
#     which adds O_NONBLOCK so the open returns and then refuses every type
#     but a regular file.
#   - A hardlink is not a link at all as far as openat() is concerned: it is
#     the file, under a second name, and nothing distinguishes one a guest
#     made to a host file from an ordinary rootfs entry. Writing through a
#     name can therefore always land outside the container, so every write
#     creates a *new* inode instead — open_new_at() is O_EXCL, and a
#     destination that may already exist is renamed into place.
#
# Directory fds are closed as each recursion unwinds, so the number open
# at any moment is the depth of the tree, not its size.

import errno
import os
import shutil
import stat

# Directories are opened readable because scandir() needs it; O_PATH is
# not enough. A directory that cannot be opened this way (execute-only)
# surfaces as PermissionError, which callers report and skip.
_O_RD_DIR = os.O_RDONLY | os.O_DIRECTORY

# O_PATH opens a directory whatever its permission bits say, which is what
# lets _make_readable_at() chmod a directory it cannot otherwise open. Such
# an fd cannot be read or fchmod'ed, only pointed at (see _chmod_fd).
_O_PATH_DIR = (getattr(os, "O_PATH", 0) or os.O_RDONLY) | os.O_DIRECTORY

# openat() declining to follow a symlink reports one of these.
REFUSED = frozenset((errno.ELOOP, errno.ENOTDIR))

# Suffix for the sibling file a replacing copy writes before renaming it
# into place. `sync` has its own (.~pd_sync) so a leftover says which pass
# left it behind.
TMP_SUFFIX = ".~pd_copy"

# Longest single path component Linux accepts, in bytes.
NAME_MAX = 255

_BUFSIZE = 256 * 1024


def is_refusal(exc: OSError) -> bool:
    """True when *exc* is openat() refusing to follow a symlink."""
    return exc.errno in REFUSED


def temp_name(name: str, suffix: str) -> str:
    """*name* with *suffix* appended, trimmed to fit in one component.

    A name is already at the filesystem's limit as often as not — 255
    bytes is a lot of characters but not an unreachable number for a
    cache key, a mangled build artefact or an encrypted filename — and
    appending nine more bytes to one turns the write into ENAMETOOLONG.
    Trimming the stem instead keeps a temp file that fits next to any
    entry the source can hold, whatever its name.

    The trim is done on the encoded bytes, since NAME_MAX counts those,
    and a multi-byte character cut in half comes back through
    os.fsdecode() as surrogates that re-encode to exactly the bytes it
    was cut to. Uniqueness costs nothing here: entries are written one
    at a time, and the temp file is renamed into place before the next
    one is opened.
    """
    room = NAME_MAX - len(os.fsencode(suffix))
    encoded = os.fsencode(name)
    if len(encoded) <= room:
        return name + suffix
    return os.fsdecode(encoded[:room]) + suffix


# ---------------------------------------------------------------------------
# Opening
# ---------------------------------------------------------------------------

def opendir(path: str) -> int:
    """Open *path* as a directory fd."""
    return os.open(path, _O_RD_DIR)


def opendir_at(dir_fd: int, name: str) -> int:
    """Open subdirectory *name* under dir_fd, refusing a symlink."""
    return os.open(name, _O_RD_DIR | os.O_NOFOLLOW, dir_fd=dir_fd)


def reopen(dir_fd: int, name: str = "") -> int:
    """Return a readable directory fd for *name* under dir_fd.

    With no name, re-opens the directory dir_fd itself refers to. That
    is how a pin taken with O_PATH (which cannot be scanned) is turned
    into something this module can walk, without going through /proc.
    """
    if name:
        return opendir_at(dir_fd, name)
    return os.open(os.curdir, _O_RD_DIR, dir_fd=dir_fd)


def open_file_at(dir_fd: int, name: str, flags: int, mode: int = 0o644) -> int:
    """Open the file *name* under dir_fd, never following a symlink."""
    return os.open(name, flags | os.O_NOFOLLOW, mode, dir_fd=dir_fd)


def open_regular_at(dir_fd: int, name: str, flags: int, mode: int = 0o644):
    """Open *name* under dir_fd as a regular file. Returns (fd, stat).

    O_NOFOLLOW alone is not enough for an endpoint a container can prepare.
    It keeps a planted symlink from being followed but says nothing about a
    named pipe, and opening one blocks until a peer appears — a peer a
    hostile guest simply never provides, which hangs the command for as long
    as the user leaves it running. O_NONBLOCK makes that open return instead
    (with ENXIO for a write to a readerless pipe), and the fstat that follows
    refuses every remaining type, so a device, a socket, or a pipe that does
    have a reader attached cannot be read or written either.

    The flag has no effect on a regular file, which is all that gets past
    here, so it is left set rather than cleared again.
    """
    fd = open_file_at(dir_fd, name, flags | os.O_NONBLOCK, mode)
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode):
            raise OSError(errno.EINVAL, "not a regular file", name)
    except BaseException:
        os.close(fd)
        raise
    return fd, st


def open_new_at(dir_fd: int, name: str, mode: int = 0o644):
    """Create *name* under dir_fd as a brand-new file. Returns (fd, stat).

    O_EXCL, so no entry already carrying the name is ever written *through*.
    That is the one thing O_NOFOLLOW cannot give: a hardlink is
    indistinguishable from an ordinary file, and a guest that links a host
    file into its own rootfs under the name a transfer is about to write
    leaves nothing to refuse — an O_TRUNC write lands on the host's inode
    and rewrites it. Creating a fresh inode instead keeps every write inside
    the directory the caller pinned.

    A leftover from an interrupted run is unlinked and the create retried
    once. Unlinking removes the *name*; whatever else the inode is linked
    from keeps its content, which is exactly the point.
    """
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    try:
        return open_regular_at(dir_fd, name, flags, mode)
    except FileExistsError:
        os.unlink(name, dir_fd=dir_fd)
        return open_regular_at(dir_fd, name, flags, mode)


def unlink_quietly(dir_fd: int, name: str) -> None:
    """Remove *name* under dir_fd, ignoring failure — for temp-file cleanup."""
    try:
        os.unlink(name, dir_fd=dir_fd)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Inspecting
# ---------------------------------------------------------------------------

def listdir_at(dir_fd: int) -> list:
    """Return the sorted entry names of the directory dir_fd refers to."""
    with os.scandir(dir_fd) as it:
        return sorted(entry.name for entry in it)


def lstat_at(dir_fd: int, name: str):
    """stat *name* under dir_fd without following a final symlink."""
    return os.stat(name, dir_fd=dir_fd, follow_symlinks=False)


def exists_at(dir_fd: int, name: str) -> bool:
    """True when *name* exists under dir_fd, symlinks included."""
    try:
        lstat_at(dir_fd, name)
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# Metadata
# ---------------------------------------------------------------------------

def _copy_xattrs(src_fd: int, dst_fd: int) -> None:
    if not hasattr(os, "listxattr"):
        return
    try:
        names = os.listxattr(src_fd)
    except OSError:
        return
    for name in names:
        try:
            os.setxattr(dst_fd, name, os.getxattr(src_fd, name))
        except OSError:
            pass


def copy_metadata(src_fd: int, dst_fd: int, src_st=None) -> None:
    """Apply src's mode, timestamps and xattrs to the open dst fd.

    This is shutil.copystat() expressed against file descriptors, so no
    path — and therefore no symlink — is involved.
    """
    if src_st is None:
        src_st = os.fstat(src_fd)
    try:
        os.fchmod(dst_fd, stat.S_IMODE(src_st.st_mode))
    except OSError:
        pass
    try:
        os.utime(dst_fd, ns=(src_st.st_atime_ns, src_st.st_mtime_ns))
    except OSError:
        pass
    _copy_xattrs(src_fd, dst_fd)


def set_times_at(dir_fd: int, name: str, src_st) -> None:
    """Apply src_st's timestamps to *name* without following a symlink."""
    try:
        os.utime(name, ns=(src_st.st_atime_ns, src_st.st_mtime_ns),
                 dir_fd=dir_fd, follow_symlinks=False)
    except (OSError, NotImplementedError):
        pass


def _chmod_fd(fd: int, mode: int) -> bool:
    """Set *mode* on the inode *fd* refers to. True when it took.

    fchmod() covers an ordinary descriptor but fails with EBADF on an
    O_PATH one — and O_PATH is what paths.pin_path() hands out, so every
    chmod aimed at a copy or sync *endpoint* used to be silently swallowed
    by the caller's `except OSError`. The fallback names the same descriptor
    through /proc, which works whatever the flags and whatever the
    directory's own permission bits are.

    Both forms name a descriptor rather than a path, so neither can be
    redirected by a symlink appearing under the entry's name.
    """
    try:
        os.fchmod(fd, mode)
        return True
    except OSError:
        pass
    try:
        os.chmod(f"/proc/self/fd/{fd}", mode)
        return True
    except OSError:
        return False


def make_writable(dir_fd: int) -> None:
    """Add u+rwx to the directory dir_fd refers to, best effort."""
    try:
        st = os.fstat(dir_fd)
    except OSError:
        return
    _chmod_fd(dir_fd, stat.S_IMODE(st.st_mode) | stat.S_IRWXU)


def _make_readable_at(dir_fd: int, name: str, mode: int) -> None:
    """Add u+rwx to the directory *name* under dir_fd, best effort.

    The mode is applied to a descriptor, never to the name: os.chmod() on
    the name follows a symlink (Linux has no AT_SYMLINK_NOFOLLOW for
    fchmodat), so an entry the caller lstat'ed as a directory and that a
    guest then replaced with a link would have its target chmod'ed — an
    arbitrary host file, with bits the guest chose by picking the mode of
    the directory it planted. Opening O_PATH|O_NOFOLLOW instead refuses the
    link outright and needs no permission on the directory itself, which is
    the whole reason this is reached.
    """
    try:
        fd = os.open(name, _O_PATH_DIR | os.O_NOFOLLOW, dir_fd=dir_fd)
    except OSError:
        return
    try:
        _chmod_fd(fd, mode)
    finally:
        os.close(fd)


# ---------------------------------------------------------------------------
# Copying
# ---------------------------------------------------------------------------

def copy_data(src_fd: int, dst_fd: int) -> None:
    """Copy the contents of one open file to another."""
    with open(src_fd, "rb", closefd=False) as fin, \
            open(dst_fd, "wb", closefd=False) as fout:
        shutil.copyfileobj(fin, fout, _BUFSIZE)


def copy_file_at(src_dir_fd: int, src_name: str,
                 dst_dir_fd: int, dst_name: str, src_st=None, *,
                 replace: bool = False) -> None:
    """Copy one regular file between two pinned directories.

    Both ends go through open_regular_at(), so neither a symlink nor a pipe
    planted at either name is followed, written through, or waited on, and
    the destination is always a new inode (see open_new_at) so neither is a
    hardlink.

    Pass replace=True when the destination may already exist — a copy onto
    a named file. The content then goes to a sibling temp file that is
    renamed into place, which also makes the write atomic: an interrupted
    copy leaves the old file rather than a truncated one. The cost is that a
    hardlinked destination loses its link, the unavoidable price of not
    being able to tell a guest's planted link from a legitimate one.

    A destination that is anything *but* a regular file is refused rather
    than replaced. The rename could not have followed it either, so this is
    for the message and for declining to win a race quietly: the resolve
    already followed whatever link stood there, so one standing there now
    was planted since, and a pipe or a device is not something a copy has
    any business overwriting without saying so.

    Without replace the create is plain O_EXCL, which is all copy_tree_at
    needs: every directory it writes into was just made by mkdir, so
    nothing can legitimately be there.
    """
    sfd, sfd_st = open_regular_at(src_dir_fd, src_name, os.O_RDONLY)
    try:
        if src_st is None:
            src_st = sfd_st
        if replace:
            try:
                dst_st = lstat_at(dst_dir_fd, dst_name)
            except OSError:
                dst_st = None
            if dst_st is not None and not stat.S_ISREG(dst_st.st_mode):
                raise OSError(errno.EEXIST,
                              "destination exists and is not a regular file",
                              dst_name)
        name = temp_name(dst_name, TMP_SUFFIX) if replace else dst_name
        try:
            dfd, _ = open_new_at(dst_dir_fd, name,
                                 stat.S_IMODE(src_st.st_mode))
            try:
                copy_data(sfd, dfd)
                copy_metadata(sfd, dfd, src_st)
            finally:
                os.close(dfd)
            if replace:
                os.replace(name, dst_name,
                           src_dir_fd=dst_dir_fd, dst_dir_fd=dst_dir_fd)
        except BaseException:
            if replace:
                unlink_quietly(dst_dir_fd, name)
            raise
    finally:
        os.close(sfd)


def copy_symlink_at(src_dir_fd: int, src_name: str,
                    dst_dir_fd: int, dst_name: str, src_st=None) -> None:
    """Recreate a symlink at the destination, target verbatim."""
    target = os.readlink(src_name, dir_fd=src_dir_fd)
    os.symlink(target, dst_name, dir_fd=dst_dir_fd)
    if src_st is not None:
        set_times_at(dst_dir_fd, dst_name, src_st)


def close_frames(stack) -> None:
    """Close the fds an interrupted walk still holds, ignoring failures.

    Every walk that carries directories on an explicit stack — here and in
    `sync` — lays its frames out the same way: the first two slots are the
    level's descriptors (the second None for a walk that needs only one),
    and the last is an `owned` flag, True for a level the walk opened for
    itself and False for the caller's fds, which stay open. A frame is
    pushed before its second descriptor is filled in, so that slot may
    still be None when an error lands between the two opens.
    """
    for frame in stack:
        if not frame[-1]:
            continue
        for fd in (frame[1], frame[0]):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass


def copy_tree_at(src_dir_fd: int, dst_dir_fd: int, *, rel: str = "",
                 on_entry=None, on_skip=None) -> None:
    """Recursively copy the contents of one directory into another.

    Mirrors shutil.copytree(symlinks=True): symlinks are recreated as
    symlinks and never descended into, and modes and timestamps are
    preserved. Unlike copytree, a device/FIFO/socket is reported to
    on_skip and left out rather than aborting the whole transfer — the
    same choice `backup` and `sync` already make.

    on_entry(rel_path) is called for each file and symlink written.

    The descent is an explicit stack rather than recursion. How deep a
    tree goes is the guest's to decide, and a thousand nested directories
    — which a container can create in a second — used to exhaust the
    interpreter's own stack and end the command in a traceback, since
    RecursionError is not an OSError and no caller's net caught it. One
    frame per level holds that level's two fds and the entries it has
    left, so the fds open at any moment are still the depth of the tree.
    """
    # Frame layout: [src_fd, dst_fd, rel, pending names, src_st, owned].
    # src_st is the source directory's lstat, applied to the destination
    # once that level's contents are in; the caller's frame carries None
    # for it and owns its own fds (see close_frames).
    stack = [[src_dir_fd, dst_dir_fd, rel, None, None, False]]
    try:
        while stack:
            frame = stack[-1]
            src_fd, dst_fd, cur, pending, dir_st, owned = frame
            if pending is None:
                pending = frame[3] = listdir_at(src_fd)
                pending.reverse()       # pop() from the end, in name order
            if not pending:
                stack.pop()
                if owned:
                    try:
                        # After the contents: writing them bumps the
                        # mtime, and the mode must not be applied any
                        # earlier — a source directory that is not
                        # writable itself (0555 and friends) would
                        # reject its own contents. copytree had the same
                        # two-step shape: makedirs() then copystat().
                        copy_metadata(src_fd, dst_fd, dir_st)
                    finally:
                        os.close(dst_fd)
                        os.close(src_fd)
                continue

            name = pending.pop()
            src_st = lstat_at(src_fd, name)
            mode = src_st.st_mode
            child = f"{cur}/{name}" if cur else name

            if stat.S_ISLNK(mode):
                copy_symlink_at(src_fd, name, dst_fd, name, src_st)
                if on_entry:
                    on_entry(child)
            elif stat.S_ISDIR(mode):
                # Created writable, sealed on the way back up: mkdir's
                # mode is masked by the umask and so cannot preserve the
                # source mode on its own.
                os.mkdir(name, 0o700, dir_fd=dst_fd)
                sub_src = opendir_at(src_fd, name)
                # Pushed before the second open, so a failure there
                # leaves the first fd on the stack for close_frames.
                stack.append([sub_src, None, child, None, src_st, True])
                stack[-1][1] = opendir_at(dst_fd, name)
            elif stat.S_ISREG(mode):
                copy_file_at(src_fd, name, dst_fd, name, src_st)
                if on_entry:
                    on_entry(child)
            elif on_skip:
                on_skip(child)
    except BaseException:
        close_frames(stack)
        raise


# ---------------------------------------------------------------------------
# Removing
# ---------------------------------------------------------------------------

def _unlink_at(dir_fd: int, name: str, is_dir: bool, force: bool) -> None:
    try:
        if is_dir:
            os.rmdir(name, dir_fd=dir_fd)
        else:
            os.unlink(name, dir_fd=dir_fd)
    except PermissionError:
        if not force:
            raise
        make_writable(dir_fd)
        if is_dir:
            os.rmdir(name, dir_fd=dir_fd)
        else:
            os.unlink(name, dir_fd=dir_fd)


def _opendir_for_removal(dir_fd: int, name: str, st, force: bool) -> int:
    """Open the directory *name* under dir_fd so its contents can go."""
    try:
        return opendir_at(dir_fd, name)
    except PermissionError:
        if not force:
            raise
        # Cannot descend: make the entry itself readable from here. Through
        # a descriptor, not through its name — see _make_readable_at.
        _make_readable_at(dir_fd, name,
                          stat.S_IMODE(st.st_mode) | stat.S_IRWXU)
        return opendir_at(dir_fd, name)


def rmtree_at(dir_fd: int, name: str, *, force: bool = False) -> None:
    """Remove *name* under dir_fd, descending without following symlinks.

    A symlink is unlinked, never traversed, so this cannot reach outside
    the tree it was pointed at. With force=True an unwritable directory
    is chmod'ed and retried, which is what `sync --delete` needs.

    The descent is an explicit stack for the reason copy_tree_at's is: the
    tree is guest content, and one deeper than the interpreter's recursion
    limit used to end `sync --delete` in a traceback rather than a message.
    """
    try:
        st = lstat_at(dir_fd, name)
    except FileNotFoundError:
        return

    if not stat.S_ISDIR(st.st_mode):
        _unlink_at(dir_fd, name, False, force)
        return

    # Frame layout: [fd, None, parent fd, own name, pending names, owned].
    # Every frame here opened its own fd; the parent fd and name are what
    # the level is removed by once it has been emptied.
    stack = [[_opendir_for_removal(dir_fd, name, st, force), None,
              dir_fd, name, None, True]]
    try:
        while stack:
            frame = stack[-1]
            fd, _, parent_fd, entry, pending, _ = frame
            if pending is None:
                pending = frame[4] = listdir_at(fd)
                pending.reverse()
            if not pending:
                stack.pop()
                os.close(fd)
                _unlink_at(parent_fd, entry, True, force)
                continue

            child = pending.pop()
            try:
                child_st = lstat_at(fd, child)
            except FileNotFoundError:
                continue            # went away on its own; nothing to do
            if not stat.S_ISDIR(child_st.st_mode):
                _unlink_at(fd, child, False, force)
                continue
            sub = _opendir_for_removal(fd, child, child_st, force)
            stack.append([sub, None, fd, child, None, True])
    except BaseException:
        close_frames(stack)
        raise
