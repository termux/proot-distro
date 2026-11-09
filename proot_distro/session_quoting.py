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

# Architecture: POSIX shell double-quoting for --get-proot-cmd output.
# We keep this isolated from shlex.quote because shlex prefers single
# quotes and we want double quotes so users can read the produced
# command and tweak variable references.


_SAFE_CHARS = frozenset(
    "abcdefghijklmnopqrstuvwxyz"
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    "0123456789"
    "_-+./:@="
)


def dq(s: str) -> str:
    """Return s in double quotes for a POSIX shell, quoting only when needed."""
    if s and all(c in _SAFE_CHARS for c in s):
        return s
    escaped = (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("$", "\\$")
         .replace("`", "\\`")
    )
    return f'"{escaped}"'
