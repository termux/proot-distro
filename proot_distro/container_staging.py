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

"""Staging directories that new containers are built in.

`install`, and `restore` to a name that holds no container, used to write
the new container in place, under containers/<name>. From the first
member of the image to the last, that tree was a container to every
other command: `list` showed it, `login` entered it, and the next install
of the name was refused. A failure removed it again, in a finally, but a
process killed outright runs no finally: the API's SIGKILL once its grace
period is up, a second Ctrl-C there, Android reclaiming Termux. It left a
half-unpacked rootfs, with no manifest, that looked installed.

A new container is built in a staging directory beside the containers
instead, and renamed onto its name as the very last step:

    CONTAINERS_DIR/.pd-staging-<pid>-<start>.<8 hex digits>

It is named and locked the way a build's scratch root is
(build_scratch.py), so a staging directory whose process is gone is
swept by the next install or restore, and by `clear-cache`
(reclaim_staging). The name begins with a dot, which no container name
may (names.py), so no command lists it or accepts it as a container. And
it is in CONTAINERS_DIR itself, so the rename never crosses a filesystem.

rename(2) never follows the name it lands on. It replaces an empty
directory and refuses anything else, so a name taken while the container
was being built, by a guest planting a link or a directory under it, ends
the commit in an error with nothing written through it. Both names are
checked against the staging directory's own descriptor, before the rename
and after it, so a staging directory swapped for something else is
refused rather than published. CONTAINERS_DIR is guest-writable on
Termux, as it always was: everything inside the staging directory is
written through descriptors below the one made here, exactly as the
in-place install wrote below containers/<name>.

`reset`, and `restore` over an existing container, remove the old
container completely before they build the new one (clear_container), so
the two never take up space at the same time. The old container is
renamed aside first, under a staging name of this process, and removed
from there: at every moment the name holds the complete old container,
nothing, or the complete new one, and a process killed during the removal
leaves the rest to the sweep. Once the old container is gone, a failure
loses it; the callers say how to get it back.
"""

import errno
import os
import re
import stat

from proot_distro import dirfd, statedir
from proot_distro.build_scratch import (
    make_root_in, reclaim_roots, scratch_name,
)
from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.interrupts import finish_cleanup
from proot_distro.message import quote_path
from proot_distro.operation_errors import (
    AlreadyExistsFailure, OperationFailure,
)
from proot_distro.paths import container_entry_lstat, open_container_dir
from proot_distro.progress import clear_bar, draw_count_bar

_PREFIX = ".pd-staging"
_STAGING_NAME = re.compile(
    r"\.pd-staging-([0-9]{1,10})(?:-([0-9]{1,20}))?\.[0-9a-f]{8}"
)

# What rename(2) answers when the name it would land on is taken: a
# directory with something in it, or something that is not a directory.
_TAKEN = (errno.ENOTEMPTY, errno.EEXIST, errno.ENOTDIR, errno.EISDIR)

# Fresh names to move an old container aside to, before giving up. Each
# holds 32 random bits, so a clash means a planted entry.
_ASIDE_ATTEMPTS = 16


def _lstat_at(dir_fd: int, name: str):
    try:
        return dirfd.lstat_at(dir_fd, name)
    except OSError:
        return None


def _same(entry, own) -> bool:
    return (entry is not None
            and (entry.st_dev, entry.st_ino) == (own.st_dev, own.st_ino))


class Staging:
    """One staging directory, open and locked. close() it when done.

    *fd* is the directory itself: what the new container's directory
    will be. The rootfs, the manifest and the sysdata stubs are written
    below it, by descriptor, and nothing names it by path until commit()
    has renamed it onto the container's name.
    """

    def __init__(self, containers_fd: int, name: str, fd: int) -> None:
        self._containers_fd = containers_fd
        self.name = name
        self.fd = fd

    @property
    def path(self) -> str:
        """Where the staging directory is, for messages and path walks."""
        return os.path.join(CONTAINERS_DIR, self.name)

    @property
    def rootfs_path(self) -> str:
        """Where the rootfs being built is, by path. See *fd* first."""
        return os.path.join(self.path, "rootfs")

    def open_rootfs(self) -> int:
        """Make and open rootfs/ below the staging directory."""
        return dirfd.descend_at(self.fd, ("rootfs",), create=True)

    def commit(self, target: str) -> None:
        """Rename the staging directory onto containers/<target>.

        Refused, with nothing renamed, if the staging name no longer
        leads to this directory. Refused by rename(2) itself if *target*
        is taken by anything but an empty directory. Refused after the
        fact if *target* is not this directory once renamed, which only
        a concurrent rename can arrange.
        """
        own = os.fstat(self.fd)
        if not _same(_lstat_at(self._containers_fd, self.name), own):
            raise OperationFailure(
                f"the directory container '{target}' was being built in "
                f"was replaced; nothing was installed."
            )
        try:
            os.rename(self.name, target,
                      src_dir_fd=self._containers_fd,
                      dst_dir_fd=self._containers_fd)
        except OSError as exc:
            if exc.errno not in _TAKEN:
                raise
            raise AlreadyExistsFailure(
                f"'{target}' was taken while the container was being "
                f"built, so it was not installed. Remove what is there "
                f"with '{PROGRAM_NAME} remove {target}' and try again."
            ) from exc
        if not _same(_lstat_at(self._containers_fd, target), own):
            raise OperationFailure(
                f"container '{target}' was replaced as it was installed."
            )

    def discard(self, context, *, phase, resource, interrupted) -> None:
        """Remove the staging directory and everything built in it.

        The tree is whatever the image or the archive produced so far: it
        can be deeper than the interpreter's stack and hold directories
        the image sealed, which remove_state_tree() handles, and it is
        reached through the same O_NOFOLLOW walk as every state path.
        See remove_left().
        """
        remove_left(self.name, "the unfinished container", context,
                    phase=phase, resource=resource, interrupted=interrupted)

    def close(self) -> None:
        for fd in (self.fd, self._containers_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def remove_left(name: str, what: str, context, *, phase, resource,
                interrupted=False, on_remove=None) -> None:
    """Remove CONTAINERS_DIR/<name>, a staging name, and say what is left.

    *what* names it for the user: "the unfinished container", "the old
    container". finish_cleanup() lets a first interrupt wait for the
    removal and a second one stop it. Whatever is left is no container,
    and the next install, restore or clear-cache removes it once this
    process has ended; the user is told so. *on_remove* is called with
    each entry removed.
    """
    path = os.path.join(CONTAINERS_DIR, name)
    try:
        finish_cleanup(
            lambda: statedir.remove_state_tree(path, on_remove=on_remove),
            interrupted=interrupted,
        )
    finally:
        if container_entry_lstat(name) is not None:
            context.error(
                f"What {what} left in '{quote_path(path)}' was not fully "
                f"removed. The next install or restore removes it, and "
                f"so does '{PROGRAM_NAME} clear-cache'.",
                phase=phase, resource=resource,
            )


def _move_aside(containers_fd: int, target: str) -> str:
    """Rename the entry at containers/<target> to a fresh staging name.

    A staging name of this process, so that a process killed before the
    entry is removed leaves it to the sweep. Whatever is there is
    renamed, never followed: rename(2) moves a link planted under the
    name as a link, which the removal then unlinks.
    """
    for _attempt in range(_ASIDE_ATTEMPTS):
        aside = scratch_name(_PREFIX)
        try:
            os.rename(target, aside,
                      src_dir_fd=containers_fd, dst_dir_fd=containers_fd)
        except OSError as exc:
            if exc.errno in _TAKEN:
                continue
            raise
        return aside
    raise FileExistsError(errno.EEXIST, "no free name to move it to")


def clear_container(name: str, context, *, phase, resource) -> None:
    """Remove containers/<name> completely, to build a new one in its place.

    For `reset` and for `restore` over an existing container, under the
    container's lock: the old container goes before the new one is
    written, so the two never take up space at the same time. It is
    renamed aside in one step, so from then on nothing is listed or
    entered under the name, and removed from there, with a running count
    like the one the in-place clear used to draw. A first Ctrl-C lets the
    removal finish and a second one stops it (remove_left); either way
    the name is already free, and what is left goes to the sweep.
    """
    containers_fd = statedir.open_state_dir(CONTAINERS_DIR)
    try:
        aside = _move_aside(containers_fd, name)
    finally:
        os.close(containers_fd)
    count = 0
    clear_bar()

    def counted(_rel) -> None:
        nonlocal count
        count += 1
        draw_count_bar(count, count, label="Removing old rootfs",
                       phase=phase, resource=resource)

    try:
        remove_left(aside, "the old container", context, phase=phase,
                    resource=resource, on_remove=counted)
    finally:
        clear_bar()


def create_staging() -> Staging:
    """Make, open and lock a fresh staging directory in CONTAINERS_DIR.

    Made with mode 0777 before the umask, the mode open_container_dir()
    gives a container's directory, since this one becomes it.
    """
    containers_fd = statedir.open_state_dir(CONTAINERS_DIR, create=True)
    try:
        name, fd = make_root_in(containers_fd, _PREFIX, mode=0o777)
    except BaseException:
        os.close(containers_fd)
        raise
    return Staging(containers_fd, name, fd)


def refuse_leftovers(name: str) -> None:
    """Raise unless containers/<name> is free for a new container.

    Free means nothing is there, or an empty directory: the two things
    commit() can rename onto. Anything else -- a directory holding
    something but no rootfs, which is what an interrupted removal
    leaves, or an entry that is not a directory -- is refused up front,
    before anything is downloaded or extracted, with the command that
    clears it. The caller has asked whether a container is installed
    under the name already; this answers for what is not one.
    """
    entry = container_entry_lstat(name)
    if entry is None:
        return
    if stat.S_ISDIR(entry.st_mode):
        try:
            fd = open_container_dir(name, raise_errors=True)
        except FileNotFoundError:
            return
        try:
            if not dirfd.listdir_at(fd):
                return
        finally:
            os.close(fd)
    raise AlreadyExistsFailure(
        f"'{name}' holds the leftovers of an incomplete container. "
        f"Remove them with '{PROGRAM_NAME} remove {name}'."
    )


def _open_containers():
    # ValueError: CONTAINERS_DIR outside the state roots, which a caller
    # that narrows them (tests do) can arrange. Nothing to sweep there.
    try:
        return statedir.open_state_dir(CONTAINERS_DIR)
    except (OSError, ValueError):
        return None


def reclaim_staging(announce=None) -> int:
    """Remove the staging directories of installs and restores that died.

    build_scratch.reclaim_roots() decides, by the same rules as for a
    build's scratch: the name, this user's ownership, the maker gone and
    the lock free. *announce(count)* is called before anything is
    removed. Returns how many were removed.
    """
    return reclaim_roots((_open_containers,), _STAGING_NAME, announce)


__all__ = (
    "Staging",
    "clear_container",
    "create_staging",
    "reclaim_staging",
    "refuse_leftovers",
    "remove_left",
)
