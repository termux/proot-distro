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

# Architecture: Pure parsing helpers for Docker/OCI image references.
# No network calls, no cache access — kept in their own module so any
# layer that just needs to crack open a tag (e.g. the help text, the
# build CLI) doesn't drag in the registry transport stack.


# proot-distro arch name → (Docker architecture, optional variant)
ARCH_TO_DOCKER = {
    "aarch64": ("arm64",   ""),
    "arm":     ("arm",     "v7"),
    "i686":    ("386",     ""),
    "x86_64":  ("amd64",   ""),
    "riscv64": ("riscv64", ""),
}


def parse_image_ref(image_ref: str) -> tuple:
    """Parse an image reference into (registry, repo, tag).

    Docker Hub images (no registry host):
      'ubuntu'           -> ('', 'library/ubuntu', 'latest')
      'ubuntu:24.04'     -> ('', 'library/ubuntu', '24.04')
      'myuser/img:1.0'   -> ('', 'myuser/img', '1.0')
      'docker.io/library/ubuntu:24.04' -> ('', 'library/ubuntu', '24.04')

    Custom registry images (host contains a dot or colon):
      'ghcr.io/foo/bar:latest' -> ('ghcr.io', 'foo/bar', 'latest')
    """
    parts = image_ref.split("/", 1)
    if len(parts) == 2 and ("." in parts[0] or ":" in parts[0]):
        registry = parts[0]
        remainder = parts[1]
    else:
        registry = ""
        remainder = image_ref

    # 'docker.io' and 'index.docker.io' are user-facing aliases for
    # Docker Hub. The actual API host is 'registry-1.docker.io' (and
    # auth lives at 'auth.docker.io'), so route these through the
    # default path by clearing the registry — otherwise a probe would
    # hit the marketing site at https://docker.io/ and decode HTML.
    if registry in ("docker.io", "index.docker.io"):
        registry = ""

    if ":" in remainder:
        name, tag = remainder.rsplit(":", 1)
    else:
        name, tag = remainder, "latest"

    if not registry:
        repo = name if "/" in name else f"library/{name}"
    else:
        repo = name

    return registry, repo, tag


def derive_alias(image_ref: str) -> str:
    """Derive a short local alias from an image reference.

    'ubuntu:24.04'             -> 'ubuntu'
    'myuser/img:tag'           -> 'img'
    'ghcr.io/foo/bar:tag'      -> 'bar'
    'localhost:5000/foo:tag'   -> 'foo'

    Routes through parse_image_ref so that a colon in a registry host
    (port number) is not mistaken for the start of the tag.
    """
    _registry, repo, _tag = parse_image_ref(image_ref)
    return repo.split("/")[-1]
