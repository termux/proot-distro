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

# Architecture: ANSI color constants and a minimal msg() helper. Colors are
# enabled only when stderr is a TTY and PD_FORCE_NO_COLORS is unset. The C
# dict maps symbolic names to escape sequences so callers don't deal with
# raw ANSI codes. Every entry starts with _RST so transitions implicitly
# reset attributes.
#
# tty_safe_for_writes() lets callers detect when stderr's TTY has been
# taken over by another process for interactive input — e.g., the pinentry
# triggered by piping a backup into 'gpg -c'. In that state any of our
# writes (info lines, progress bars) would land on top of the other
# program's display. msg() and the backup progress bar consult this so
# that piping into curses/no-echo consumers stays clean.
#
# A process-global "quiet" flag is also exported: set_quiet(True) (called
# by command handlers when --quiet was passed) makes is_quiet() return True
# so progress-bar code paths in helpers can skip their writes. msg() itself
# is unaffected — error messages must always reach the user.

import os
import sys

try:
    import termios
except ImportError:
    termios = None

_RST       = "\033[0m"
_BOLD      = "\033[1m"
_ITALIC    = "\033[3m"
_UNDERLINE = "\033[4m"
_RED       = "\033[31m"
_GREEN     = "\033[32m"
_YELLOW    = "\033[33m"
_BLUE      = "\033[34m"
_MAGENTA   = "\033[35m"
_CYAN      = "\033[36m"
_WHITE     = "\033[37m"

_COLORS = {
    "RST":     _RST,

    "RED": _RST + _RED,
    "BRED": _RST + _BOLD + _RED,
    "IRED": _RST + _ITALIC + _RED,
    "URED": _RST + _UNDERLINE + _RED,
    "UBRED": _RST + _UNDERLINE + _BOLD + _RED,

    "GREEN": _RST + _GREEN,
    "BGREEN": _RST + _BOLD + _GREEN,
    "IGREEN": _RST + _ITALIC + _GREEN,
    "UGREEN": _RST + _UNDERLINE + _GREEN,
    "UBGREEN": _RST + _UNDERLINE + _BOLD + _GREEN,

    "YELLOW": _RST + _YELLOW,
    "BYELLOW": _RST + _BOLD + _YELLOW,
    "IYELLOW": _RST + _ITALIC + _YELLOW,
    "UYELLOW": _RST + _UNDERLINE + _YELLOW,
    "UBYELLOW": _RST + _UNDERLINE + _BOLD + _YELLOW,

    "BLUE": _RST + _BLUE,
    "BBLUE": _RST + _BOLD + _BLUE,
    "IBLUE": _RST + _ITALIC + _BLUE,
    "UBLUE": _RST + _UNDERLINE + _BLUE,
    "UBBLUE": _RST + _UNDERLINE + _BOLD + _BLUE,

    "MAGENTA": _RST + _MAGENTA,
    "BMAGENTA": _RST + _BOLD + _MAGENTA,
    "IMAGENTA": _RST + _ITALIC + _MAGENTA,
    "UMAGENTA": _RST + _UNDERLINE + _MAGENTA,
    "UBMAGENTA": _RST + _UNDERLINE + _BOLD + _MAGENTA,

    "CYAN": _RST + _CYAN,
    "BCYAN": _RST + _BOLD + _CYAN,
    "ICYAN": _RST + _ITALIC + _CYAN,
    "UCYAN": _RST + _UNDERLINE + _CYAN,
    "UBCYAN": _RST + _UNDERLINE + _BOLD + _CYAN,

    "WHITE": _RST + _WHITE,
    "BWHITE": _RST + _BOLD + _WHITE,
    "IWHITE": _RST + _ITALIC + _WHITE,
    "UWHITE": _RST + _UNDERLINE + _WHITE,
    "UBWHITE": _RST + _UNDERLINE + _BOLD + _WHITE,
}
_EMPTY = {k: "" for k in _COLORS}


def _init_colors() -> dict:
    if sys.stderr.isatty() and not os.environ.get("PD_FORCE_NO_COLORS"):
        return _COLORS
    return _EMPTY


C = _init_colors()


def tty_safe_for_writes() -> bool:
    """Return False when stderr's TTY is currently being used by another
    process for interactive input (a password prompt or a full-screen
    curses UI). Return True otherwise.

    A TTY in canonical mode with ECHO enabled is in its normal "shell"
    state. Programs that read a passphrase clear ECHO; programs that
    drive a full-screen UI clear ICANON. Either signal means another
    process has taken control of the TTY and our writes — particularly
    destructive escapes like '\\r' or '\\033[K' from a progress bar —
    would corrupt that program's display. The check looks only at the
    TTY's termios state, so it triggers for any such consumer (pinentry,
    sudo, ssh-askpass, less, etc.) without naming any specific program.
    Non-TTY stderr (file/pipe) always returns True so plain redirection
    is unaffected.
    """
    if termios is None:
        return True
    try:
        fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        return True
    try:
        if not os.isatty(fd):
            return True
    except OSError:
        return True
    try:
        attrs = termios.tcgetattr(fd)
    except (OSError, termios.error):
        return True
    lflag = attrs[3]
    return bool(lflag & termios.ECHO) and bool(lflag & termios.ICANON)


_quiet = False


def set_quiet(value: bool) -> None:
    """Enable or disable quiet mode for the rest of the process.

    Called by command handlers that accept --quiet. When enabled, log_info()
    becomes a no-op and is_quiet() returns True so progress-bar code
    paths in helpers can also bail out.
    """
    global _quiet
    _quiet = bool(value)


def is_quiet() -> bool:
    """Return True when quiet mode has been enabled for this process."""
    return _quiet


def msg(*args):
    """Print *args* to stderr after clearing any partial progress line.

    Suppressed when the TTY is currently being used by another program
    (pinentry, curses) — see tty_safe_for_writes for the rationale.

    The `\\r\\033[K` progress-line eraser is only emitted when stderr is
    a TTY: progress bars are drawn exclusively on a TTY, so off a TTY
    there is no partial line to clear and the escape would only inject
    control characters into a redirected log.
    """
    if not tty_safe_for_writes():
        return
    try:
        is_tty = sys.stderr.isatty()
    except (AttributeError, OSError, ValueError):
        is_tty = False
    if is_tty:
        sys.stderr.write("\r\033[K")
        sys.stderr.flush()
    print(*args, file=sys.stderr)


def log_info(text: str):
    """Emit a `[*] text` info line. No-op under --quiet."""
    if _quiet:
        return
    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}{text}{C['RST']}")


def log_error(text: str):
    """Emit a `[!] text` error line. Always shown — even under --quiet."""
    msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}{text}{C['RST']}")


def warn(text: str):
    """Emit a 'Warning: text' line in yellow."""
    msg(f"{C['BYELLOW']}Warning: {C['YELLOW']}{text}{C['RST']}")


def crit_error(text: str):
    """Emit an 'Error: text' line in red. Caller typically calls sys.exit(1)."""
    msg(f"{C['BRED']}Error: {C['RED']}{text}{C['RST']}")
