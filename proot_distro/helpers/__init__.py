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

# Architecture: Reusable building blocks used across multiple commands.
# Each module covers one concern:
#
#   build_cache.py   — recipe-hash → cached layer index used by `build`.
#   build_engine/    — Dockerfile interpreter (FROM/RUN/COPY/...).
#   docker/          — pure-Python OCI/Docker registry client.
#   dockerfile.py    — Dockerfile lexer + parser.
#   download.py      — HTTP GET with progress + retries.
#   layer_diff.py    — rootfs snapshot/diff, layer-tar writer (RUN step).
#   oci_writer.py    — manifest cache + OCI tarball outputs of `build`.
#   rootfs.py        — post-extraction rootfs fixups (DNS, hosts, UIDs).
#   tar_extract.py   — streaming tar -> rootfs extractor with OCI
#                      whiteout support and strip-count parameter.
#
# Keeping these in a sub-package avoids the alternative of coupling
# command modules to each other for shared low-level routines.
