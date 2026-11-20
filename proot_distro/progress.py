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

# Architecture: Shared TTY progress-bar primitives. Every long-running
# command (install, backup, restore, copy, sync, build, push) draws the same
# style of bar — a 20-cell `[####----] NN% A / B` line on stderr,
# anchored by '\r' and erased with '\033[K'. Centralising the format
# here removes ~7 near-identical copies and ensures a single place to
# adjust width, throttling, or palette.
#
# Output is gated on (a) stderr being a TTY, (b) the process not being
# in quiet mode, and (c) tty_safe_for_writes() — the latter ensures
# that progress lines do not corrupt a sibling pinentry/curses display
# when the user pipes our output into gpg, ssh-askpass, etc.

import sys
from contextlib import contextmanager
from contextvars import ContextVar

from proot_distro.message import C, is_quiet, tty_safe_for_writes


# Default redraw threshold: callers that throttle bar updates by raw bytes
# should aim for ~256 KiB between writes so the terminal isn't flooded.
REDRAW_THRESHOLD_BYTES = 262144

_event_sink = ContextVar("proot_distro_progress_event_sink", default=None)


@contextmanager
def capture_progress_events(sink):
    """Route progress to *sink(message, current, total, unit, phase, resource)*."""
    token = _event_sink.set(sink)
    try:
        yield
    finally:
        _event_sink.reset(token)


def fmt_size(n_bytes: int) -> str:
    """Return a human-readable size string (B, KiB, MiB, GiB)."""
    if n_bytes >= 1 << 30:
        return f"{n_bytes / (1 << 30):.1f} GiB"
    if n_bytes >= 1 << 20:
        return f"{n_bytes / (1 << 20):.1f} MiB"
    if n_bytes >= 1 << 10:
        return f"{n_bytes / (1 << 10):.1f} KiB"
    return f"{n_bytes} B"


class ByteCounter:
    """File wrapper that tallies bytes flowing through read()/readinto().

    Used to drive progress bars when reading from a (compressed) stream
    where the eventual decompressed total is unknown, but the raw byte
    count from disk is. Pass-through for every other attribute.
    """

    def __init__(self, fh):
        self._fh = fh
        self.count = 0

    def read(self, size=-1):
        data = self._fh.read(size)
        self.count += len(data)
        return data

    def readinto(self, buf):
        n = self._fh.readinto(buf)
        self.count += n
        return n

    def __getattr__(self, name):
        return getattr(self._fh, name)


def progress_active() -> bool:
    """Return True when terminal or structured progress is active."""
    return _event_sink.get() is not None or (
        sys.stderr.isatty() and not is_quiet()
    )


def draw_bytes_bar(
    done: int,
    total: int = 0,
    *,
    label: str = "",
    noun: str = "processed",
    phase: str = "process",
    resource=None,
) -> None:
    """Draw a [####----] progress line keyed by byte counts.

    With *total* > 0 the bar shows percent + done/total in human-readable
    sizes. With *total* == 0 the bar falls back to a counter-only display
    ("{label}: {fmt_size(done)} {noun}..."). The line is anchored with
    a leading '\\r' and trailing '\\033[K' so the terminal stays clean.

    *phase* and *resource* are used only by the structured API sink. They do
    not change terminal rendering.

    No-op when stderr is not a TTY, the process is in quiet mode, or
    another program is currently holding the TTY for interactive I/O.
    """
    sink = _event_sink.get()
    if sink is not None:
        sink(label or noun, done, total or None, "bytes", phase, resource)
        return
    if not progress_active() or not tty_safe_for_writes():
        return
    pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
    head = f"{label}: " if label else ""
    if total:
        pct = min(done * 100 // total, 100)
        bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
        line = (f"\r{pfx}{head}[{bar}] {pct:3d}%  "
                f"{fmt_size(done)} / {fmt_size(total)}\033[K{C['RST']}")
    else:
        line = (f"\r{pfx}{head}{fmt_size(done)} {noun}..."
                f"\033[K{C['RST']}")
    sys.stderr.write(line)
    sys.stderr.flush()


def draw_count_bar(
    done: int,
    total: int,
    *,
    label: str = "",
    unit: str = "files",
    phase: str = "process",
    resource=None,
) -> None:
    """Draw an item-count bar with structured *phase*/*resource* context."""
    sink = _event_sink.get()
    if sink is not None:
        sink(label or unit, done, total, unit, phase, resource)
        return
    if not progress_active() or not tty_safe_for_writes():
        return
    pfx = f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
    head = f"{label}: " if label else ""
    # Clamped like draw_bytes_bar's: a count can outrun the total it was
    # given (a source that grew since it was counted), and an unclamped
    # percentage draws a bar longer than the twenty cells it has.
    pct = min(done * 100 // total, 100) if total else 100
    bar = "#" * (pct // 5) + "-" * (20 - pct // 5)
    line = (f"\r{pfx}{head}[{bar}] {pct:3d}%  "
            f"{done} / {total} {unit}\033[K{C['RST']}")
    sys.stderr.write(line)
    sys.stderr.flush()


def clear_bar() -> None:
    """Erase the current progress line. No-op when output is inactive."""
    if _event_sink.get() is not None:
        return
    if not progress_active() or not tty_safe_for_writes():
        return
    sys.stderr.write("\r\033[K")
    sys.stderr.flush()
