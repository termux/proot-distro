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

# Architecture: Single source of truth for the container-name format rule.
# Used by every command that accepts a container name (install, remove,
# rename, reset, login, run, backup, restore) plus the build command's
# --install-as / -t/--tag aliases. Centralising the regex prevents drift
# between modules that would otherwise each copy the same pattern.

import re
import sys

from proot_distro.message import crit_error


# Container names: start with an alphanumeric, then any of letter/digit/
# underscore/dot/hyphen. Same rule used everywhere a container name is
# accepted as input or derived from a Docker image reference.
_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.\-]*$")

NAME_RULE_HINT = (
    "It must begin with a letter or digit and contain only letters, "
    "digits, underscores, dots, or hyphens."
)


def is_valid_name(name: str) -> bool:
    """Return True iff *name* satisfies the container-name regex."""
    return bool(_NAME_RE.match(name or ""))


def require_valid_name(name: str, kind: str = "container name") -> None:
    """Exit with an error when *name* is invalid; otherwise return None.

    *kind* is the noun shown in the error ("container name", "new
    container name", "--install-as value", ...). Centralising the
    message body keeps wording consistent across commands.
    """
    if not is_valid_name(name):
        crit_error(f"{kind} '{name}' is not valid. {NAME_RULE_HINT}")
        sys.exit(1)
