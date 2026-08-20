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

# Architecture: the directory proot binds into the guest as /dev/shm.
#
# Android's /dev has no usable shm, and the host's /dev is bound in
# wholesale, so the guest is given a writable directory of its own under
# that name. It used to be the rootfs's own /tmp, and that is the one
# thing it must not be: a bind source is a *name*, and proot resolves it
# when it mounts it, after this program has finished checking it.
#
# Making the directory through descriptors closes the persistent half of
# that — an image shipping `tmp -> <host home>`, or a guest leaving one
# behind between sessions, is refused rather than followed — but not the
# race. `login` holds a shared lock on purpose, so another session of the
# same container may be running while a new argv is assembled, and a
# hostile guest need only flip `/tmp` from a directory to a symlink in
# the window between the check and the exec to have the *next* session
# mount a host directory of its choosing, read-write, inside the
# container. Under --isolated that is the whole of the mode's promise
# undone, since nothing else of the host is bound there at all.
#
# So the store is a sibling of the rootfs — containers/<name>/shm, or a
# build stage's own directory — rather than a name inside it. Swapping it
# means writing to its *parent*, and no session confined to the rootfs
# can reach that: the guest sees the directory only through the bind,
# which gives no way to its parent (proot canonicalises the guest path
# first, so /dev/shm/.. is /dev). What is left is a session that already
# has $TERMUX_PREFIX bound read-write — which is to say one that is
# already outside the container, and can rewrite this program itself.
#
# The guest's own /tmp is still created, because containers have always
# had one made for them here; it is simply no longer what /dev/shm is.
# The two were the same directory before, so shared-memory files showed
# up in the guest's /tmp and, during a build, in the layer the step
# produced. Keeping them apart is also what Docker does: /dev/shm is a
# tmpfs there, and nothing a RUN step writes to it belongs in an image.

import os

from proot_distro import dirfd

# Sibling of the rootfs, like sysdata/. Removed with the container.
SHM_DIR_NAME = "shm"

# What a /dev/shm looks like from inside. The mode is applied to the
# descriptor, never to the name -- see dirfd.makedirs_under.
SHM_DIR_MODE = 0o1777

# The guest's own /tmp, which is not the shm store any more but is still
# made for a container that ships without one.
GUEST_TMP_MODE = 0o1777


def shm_dir(rootfs: str) -> str:
    """Path of the shm store belonging to *rootfs*."""
    return os.path.join(os.path.dirname(rootfs), SHM_DIR_NAME)


def make_shm_dir(rootfs: str):
    """Create the shm store next to *rootfs*. Path, or None.

    None means the name could not be validated -- a component is a
    symlink or is not a directory -- in which case the caller binds
    nothing rather than handing proot a name it could not vouch for.
    """
    return dirfd.makedirs_under(
        os.path.dirname(rootfs), (SHM_DIR_NAME,), mode=SHM_DIR_MODE,
    )


def make_guest_tmp(rootfs: str) -> None:
    """Give the guest a /tmp if it has none. Failure is not an error.

    Nothing is bound from here and nothing depends on the result, so a
    name that will not validate is simply left alone: the container
    either ships its own /tmp or does without, exactly as it would have
    without this call.
    """
    dirfd.makedirs_under(rootfs, ("tmp",), mode=GUEST_TMP_MODE)


__all__ = ("SHM_DIR_NAME", "make_guest_tmp", "make_shm_dir", "shm_dir")
