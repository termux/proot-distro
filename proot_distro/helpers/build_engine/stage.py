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


class Stage:
    """Per-FROM state for the build engine.

    Holds the rootfs path the stage works against, the evolving image
    config, the layers produced so far (each `{digest, size, diff_id}`
    in build order), and the per-stage scopes for ENV/ARG/USER/SHELL/
    WORKDIR that subsequent instructions inherit.
    """

    __slots__ = (
        "index", "name", "rootfs_dir", "image_config", "layers",
        "parent_layer_digest", "env", "args", "declared_args",
        "workdir", "user", "shell", "target_arch_pd",
    )

    def __init__(self, index, name, rootfs_dir, target_arch_pd):
        self.index = index
        self.name = name
        self.rootfs_dir = rootfs_dir
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
