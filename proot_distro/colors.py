#
# Proot-Distro - manage proot containers on Termux.
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

# Architecture: ANSI color constants and a minimal msg() helper. Colors are
# enabled only when stderr is a TTY and PD_FORCE_NO_COLORS is unset. The C
# dict maps symbolic names to escape sequences so callers don't deal with
# raw ANSI codes. Every entry starts with _RST so transitions implicitly
# reset attributes.

import os
import sys

from proot_distro.constants import PROGRAM_VERSION


_RST    = "\033[0m"
_BOLD   = "\033[1m"
_ITALIC = "\033[3m"
_RED    = "\033[31m"
_GREEN  = "\033[32m"
_YELLOW = "\033[33m"
_BLUE   = "\033[34m"
_CYAN   = "\033[36m"

_COLORS = {
    "RST":     _RST,
    "RED":     _RST + _RED,
    "BRED":    _RST + _BOLD + _RED,
    "GREEN":   _RST + _GREEN,
    "YELLOW":  _RST + _YELLOW,
    "BYELLOW": _RST + _BOLD + _YELLOW,
    "BLUE":    _RST + _BLUE,
    "CYAN":    _RST + _CYAN,
    "BCYAN":   _RST + _BOLD + _CYAN,
    "ICYAN":   _RST + _ITALIC + _CYAN,
}
_EMPTY = {k: "" for k in _COLORS}


def _init_colors() -> dict:
    if sys.stderr.isatty() and not os.environ.get("PD_FORCE_NO_COLORS"):
        return _COLORS
    return _EMPTY


C = _init_colors()


def msg(*args):
    print(*args, file=sys.stderr)


def show_version():
    msg(f"{C['ICYAN']}Proot-Distro version '{PROGRAM_VERSION}'"
        f" by Termux (@sylirre).{C['RST']}")
