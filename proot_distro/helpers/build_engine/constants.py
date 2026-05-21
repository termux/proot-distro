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

# Architecture: Constants used across the build_engine subpackage —
# Dockerfile flag tables and `needs_proot()` for the CLI's startup
# probe. No I/O, no imports of sibling submodules: this is a leaf.


# Predefined ARG keys that are always visible without explicit
# declaration in the Dockerfile (subset of Docker's "predefined"
# build args).
PREDEFINED_ARGS = frozenset({
    "TARGETPLATFORM", "TARGETOS", "TARGETARCH", "TARGETVARIANT",
    "BUILDPLATFORM", "BUILDOS", "BUILDARCH", "BUILDVARIANT",
    "HTTP_PROXY", "HTTPS_PROXY", "NO_PROXY", "FTP_PROXY", "ALL_PROXY",
    "http_proxy", "https_proxy", "no_proxy", "ftp_proxy", "all_proxy",
})

# Instructions whose argument values undergo variable expansion before
# dispatch (everything except CMD/ENTRYPOINT/RUN exec-form payloads).
EXPANDS_VARS = frozenset({
    "ADD", "ARG", "ENV", "EXPOSE", "FROM", "LABEL", "STOPSIGNAL",
    "USER", "VOLUME", "WORKDIR", "COPY",
})

# Instructions that require executing `proot` against the rootfs.
PROOT_REQUIRED_INSTRUCTIONS = frozenset({"RUN"})


def needs_proot(instructions) -> bool:
    """Return True iff any instruction (including ONBUILD <inner>) is RUN."""
    for instr in instructions:
        name = instr.get("name", "")
        if name in PROOT_REQUIRED_INSTRUCTIONS:
            return True
        if name == "ONBUILD":
            inner = instr.get("value")
            if isinstance(inner, dict) and inner.get("name") in PROOT_REQUIRED_INSTRUCTIONS:
                return True
    return False
