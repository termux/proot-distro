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

# Architecture: One Stage per FROM in the Dockerfile. Tracks the
# in-progress image config, the ordered list of produced layers, and
# the per-stage scope of ENV / ARG / WORKDIR / USER / SHELL state.
#
# A stage also owns two descriptors: its own directory under the build's
# scratch root, and the rootfs inside it. They are opened once, when the
# engine creates those directories off the scratch root's own descriptor,
# and every host-side step of the build addresses the tree through them.
#
# The path is kept as well, but only for what a path is still needed for:
# messages, proot's --bind sources, PROOT_L2S_DIR -- names proot resolves
# for itself after the exec. Everything this program does on the host
# side goes through the descriptors, because `<scratch>/stage-N/rootfs`
# is a *name*, and the build re-resolved it for every snapshot, every
# cached-layer apply, every layer packed, every COPY and every RUN. The
# scratch root is 0700 but that is only the invoking user's own
# permission: a process a previous RUN step left running is that user --
# nothing kills one off Termux, --kill-on-exit being a Termux-only proot
# extension -- and on Termux the whole runtime tree is bound read-write
# into every non-isolated container, which a cross-arch RUN step makes
# certain of by binding $TERMUX_PREFIX for the emulator's loader. Moving
# the rootfs aside and leaving a symlink under the name was therefore
# enough to have the rest of the build read and write somewhere else
# entirely -- and what it reads goes into a layer `push` uploads.


import os


class Stage:
    """Per-FROM state for the build engine.

    Holds the rootfs the stage works against -- as a descriptor, plus the
    path for the things only a path can express -- the evolving image
    config, the layers produced so far (each `{digest, size, diff_id}` in
    build order), and the per-stage scopes for ENV/ARG/USER/SHELL/WORKDIR
    that subsequent instructions inherit.

    *dir_fd* is the stage's own directory, the parent of the rootfs: the
    `sysdata/` and `shm/` stores proot binds into a RUN step are siblings
    of the rootfs, made and chmod'ed there the same way `login` makes a
    container's.

    Both descriptors are the stage's for the life of the build and are
    released by close(). A caller with no descriptors to give (a test
    working on a tree it made itself) leaves them None, and every
    consumer falls back to the path form it had before.
    """

    __slots__ = (
        "index", "name", "rootfs_dir", "dir_fd", "rootfs_fd",
        "image_config", "layers",
        "parent_layer_digest", "env", "args", "declared_args",
        "workdir", "user", "shell", "target_arch_pd",
    )

    def __init__(self, index, name, rootfs_dir, target_arch_pd,
                 *, dir_fd=None, rootfs_fd=None):
        self.index = index
        self.name = name
        self.rootfs_dir = rootfs_dir
        self.dir_fd = dir_fd
        self.rootfs_fd = rootfs_fd
        self.image_config = {"config": {}}
        self.layers = []
        self.parent_layer_digest = ""
        self.env = {}
        self.args = {}
        self.declared_args = set()
        self.workdir = "/"
        self.user = ""
        self.shell = ["/bin/sh", "-c"]
        self.target_arch_pd = target_arch_pd

    def close(self):
        """Release the two descriptors. Idempotent."""
        for attr in ("rootfs_fd", "dir_fd"):
            fd = getattr(self, attr)
            setattr(self, attr, None)
            if fd is None:
                continue
            try:
                os.close(fd)
            except OSError:
                pass
