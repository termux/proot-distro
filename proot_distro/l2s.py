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

# Architecture: proot's --link2symlink extension stores hard-link backing
# files under <rootfs>/.l2s/ and replaces the original locations with
# symlinks whose targets are absolute paths into <rootfs>. After we move
# the rootfs (rename, legacy-layout migration), every l2s symlink target
# still points at the previous location and the inner files appear
# broken.
#
# This module rewrites those symlinks in-place. The rewrite walks the
# whole tree, so we intercept SIGINT/SIGQUIT for the duration: if the
# user Ctrl-C's mid-walk we'd leave the container in a half-rewritten
# state where some inner files point at a now-missing path. Instead the
# signal handler prints a warning and the walk continues to completion.

import os
import signal
import stat

from proot_distro import dirfd
from proot_distro.message import log_info, log_error


# Prefix used by proot's --link2symlink extension when naming the
# intermediate file that stands in for a hard-link. See
# proot/src/extension/link2symlink/link2symlink.c: ".proot.l2s." in
# userland builds (the Termux default) and ".l2s." otherwise. Both
# spellings are accepted so layers produced by either build are
# recognised.
_L2S_NAME_PREFIXES = (".proot.l2s.", ".l2s.")


def resolve_l2s_target(symlink_full: str, target: str, rootfs: str):
    """Return abs path of an l2s intermediate file if `target` looks like one.

    `symlink_full` is the absolute path of the symlink whose readlink
    returned `target`; `rootfs` is the container rootfs root, used only
    to confine the resolved path to the rootfs subtree. Returns None
    when `target` is not an l2s intermediate or when the resolved path
    would escape `rootfs`.

    proot's --link2symlink extension emulates hard links by replacing
    the original path with a symlink to an intermediate file whose
    basename is ``<PREFIX><name><4-digit-count>``. The intermediate is
    itself a symlink to a final ``.NNNN``-suffixed file holding the
    actual content. The intermediate's parent directory depends on
    proot's PROOT_L2S_DIR: when set, every intermediate lives in that
    one directory (proot-distro sets it to ``<rootfs>/.l2s``); when
    unset, the intermediate is created next to the original. Detection
    is therefore by basename prefix, not by directory, so symlinks are
    recognised regardless of where the producing proot stashed them.

    The whole chain is resolved, so the returned path names the file
    holding the content rather than the intermediate that stands in
    front of it. That is what containment has to be decided on:
    normpath() collapses ".." without consulting the filesystem, so a
    chain whose first hop lands inside the rootfs and whose second leaves
    it satisfied the old prefix test. Two `ln -s` calls inside a guest
    were enough —

        innocent.txt          -> .proot.l2s.evil0001
        .proot.l2s.evil0001   -> /absolute/host/path

    — and the callers, which must follow the intermediate because that is
    what an l2s chain is, read straight through to the host file: backup
    packed its bytes into the archive under innocent.txt's name, and the
    build engine packed them into a layer that `push` then uploads to a
    registry.

    Callers open the result with open_l2s_backing() rather than by path,
    which re-walks it with O_NOFOLLOW; see there for why.
    """
    name = os.path.basename(target)
    if not name.startswith(_L2S_NAME_PREFIXES):
        return None
    if os.path.isabs(target):
        abs_target = target
    else:
        abs_target = os.path.join(os.path.dirname(symlink_full), target)
    # realpath on both sides: the rootfs prefix is composed lexically by
    # container_rootfs(), so a symlinked $HOME or ~/.local/share would
    # otherwise leave a resolved target and the root it must sit under
    # spelled differently and looking unrelated.
    real_target = os.path.realpath(abs_target)
    rootfs_real = os.path.realpath(rootfs)
    if real_target != rootfs_real and not real_target.startswith(
        rootfs_real + os.sep
    ):
        return None
    return real_target


def open_l2s_backing(rootfs: str, l2s_path: str):
    """Open the backing file resolve_l2s_target() named. (fd, stat) or None.

    Resolving a path and reading it are two steps, and between them a
    guest can swap a component for a symlink pointing out of the rootfs —
    `backup` holds only a shared lock, so a `login` session can be running
    while the archive is written. The components are therefore re-walked
    from a descriptor on the rootfs with O_NOFOLLOW, which fails on a
    swapped component instead of following it, and the caller gets a
    descriptor rather than a name to open again. Same guarantee
    paths.pin_path() gives `copy` and `sync`, for the same reason.

    open_regular_at() refuses anything that is not a regular file, so a
    FIFO planted under the name cannot block the read waiting for a peer
    that never comes.
    """
    rootfs_real = os.path.realpath(rootfs)
    rel = os.path.relpath(l2s_path, rootfs_real)
    parts = [p for p in rel.split(os.sep) if p and p != os.curdir]
    if not parts or os.pardir in parts:
        return None
    fd = None
    try:
        fd = dirfd.opendir(rootfs_real)
        for part in parts[:-1]:
            nxt = dirfd.opendir_at(fd, part)
            os.close(fd)
            fd = nxt
        return dirfd.open_regular_at(fd, parts[-1], os.O_RDONLY)
    except OSError:
        return None
    finally:
        if fd is not None:
            try:
                os.close(fd)
            except OSError:
                pass


def rewrite_l2s_targets(rootfs_fd: int, rootfs: str, old_prefix: str) -> None:
    """Rewrite every symlink under *rootfs_fd* whose target starts with
    *old_prefix*.

    The new prefix is *rootfs*, the path that descriptor was opened as.
    Errors on individual symlinks (e.g. read-only fs) are swallowed so a
    single bad entry doesn't abort the rewrite.

    The caller passes a descriptor rather than a path because it has one:
    both callers have just moved the tree and validated where it landed.
    Walking the path again would resolve `containers/<name>` afresh --
    guest-writable on Termux -- and this walk *writes*, unlinking an entry
    and creating a symlink in its place. Every entry is therefore named as
    (dir_fd, name), and a directory is descended into with O_NOFOLLOW, so
    a link met on the way is rewritten rather than followed.

    os.walk() also classified a symlink pointing at a directory as a
    directory, which left it out of the filenames it yielded and so out of
    the rewrite entirely; lstat is what decides here.

    SIGINT and SIGQUIT are intercepted for the duration of the walk:
    aborting partway through would leave dangling symlinks that point
    at the no-longer-existing *old_prefix*. The handler emits a
    warning and the operation continues to a clean state.
    """
    log_info("Updating PRoot link2symlink extension files "
             "(may take a while)...")

    def _warn_no_interrupt(_signum, _frame):
        log_error("Terminating now will leave link2symlink symlinks broken. "
                  "Please wait for the operation to complete.")

    old_sigint = signal.signal(signal.SIGINT, _warn_no_interrupt)
    old_sigquit = signal.signal(signal.SIGQUIT, _warn_no_interrupt)
    try:
        _rewrite_walk(rootfs_fd, rootfs, old_prefix)
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGQUIT, old_sigquit)


def _rewrite_walk(root_fd: int, rootfs: str, old_prefix: str) -> None:
    """Visit every entry under root_fd, rewriting the l2s symlinks."""
    # Frame layout: [fd, None, pending names, owned] — dirfd's own layout,
    # so close_frames() unwinds an interrupted walk. Only the descriptors
    # along the current path are open, and how deep the tree goes is the
    # container's business rather than the interpreter's.
    stack = [[root_fd, None, None, False]]
    try:
        while stack:
            frame = stack[-1]
            fd, _, pending, owned = frame
            if pending is None:
                try:
                    pending = frame[2] = dirfd.listdir_at(fd)
                except OSError:
                    pending = frame[2] = []
            if not pending:
                stack.pop()
                if owned:
                    os.close(fd)
                continue
            name = pending.pop()
            try:
                st = dirfd.lstat_at(fd, name)
            except OSError:
                continue
            if stat.S_ISDIR(st.st_mode):
                try:
                    stack.append([dirfd.opendir_at(fd, name), None, None, True])
                except OSError:
                    continue
            elif stat.S_ISLNK(st.st_mode):
                _rewrite_link(fd, name, rootfs, old_prefix)
    except BaseException:
        dirfd.close_frames(stack)
        raise


def _rewrite_link(dir_fd: int, name: str, rootfs: str,
                  old_prefix: str) -> None:
    """Re-root one symlink's target at *rootfs*, best effort."""
    try:
        target = os.readlink(name, dir_fd=dir_fd)
    except OSError:
        return
    if not target.startswith(old_prefix):
        return
    try:
        os.unlink(name, dir_fd=dir_fd)
        os.symlink(rootfs + target[len(old_prefix):], name, dir_fd=dir_fd)
    except OSError:
        pass
