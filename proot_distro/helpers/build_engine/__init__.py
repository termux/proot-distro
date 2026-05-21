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

# Architecture: Dockerfile interpreter, split by concern:
#
#   stage.py         — Stage class (per-FROM state container).
#   constants.py     — PREDEFINED_ARGS, EXPANDS_VARS, needs_proot().
#   errors.py        — BuildError.
#   parsing.py       — small stateless string utilities.
#   dockerignore.py  — .dockerignore pattern matcher + tiny glob.
#   users.py         — user/group name resolution against the rootfs.
#   handlers.py      — metadata-only handlers (ENV, LABEL, USER, ...).
#   copy_step.py     — COPY / ADD handler.
#   run_step.py      — RUN handler (proot exec + layer diff).
#   engine.py        — BuildEngine itself (FROM, dispatch, prescan).

from proot_distro.helpers.build_engine.constants import (
    PROOT_REQUIRED_INSTRUCTIONS,
    needs_proot,
)
from proot_distro.helpers.build_engine.engine import BuildEngine
from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.build_engine.stage import Stage

__all__ = (
    "BuildEngine",
    "BuildError",
    "PROOT_REQUIRED_INSTRUCTIONS",
    "Stage",
    "needs_proot",
)
