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
# Architecture: Presentation for `search`. helpers/docker/search.py owns
# the request and hands back validated hits; this module only decides
# how they are shown.
#
#   default   — a NAME/DESCRIPTION/STARS/PULLS/OFFICIAL table on stderr,
#               laid out like `list --image`: the fixed columns are
#               content-sized and DESCRIPTION takes what is left of the
#               terminal, truncated with an ellipsis. When even a
#               minimal description column does not fit (a phone), each
#               hit is stacked into a three-line block instead.
#   --quiet   — the bare repository name per line on stdout, ready to
#               pipe into `install` or `xargs`.
#
# Nothing here re-sanitises the strings: names are validated against
# Docker's name grammar and descriptions escaped in the search helper,
# so the only text this module adds escapes to is the user's own query.

import sys
import urllib.error

from proot_distro.constants import PROGRAM_NAME
from proot_distro.message import (
    C, msg, crit_error, log_error, log_info, quote_path, terminal_width,
)
from proot_distro.helpers.docker import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_LIMIT_MAX,
    SEARCH_PAGE_MAX,
    search_images,
)

# Columns of the results table. DESCRIPTION is the flexible one.
_HEADERS = ("NAME", "DESCRIPTION", "STARS", "PULLS", "OFFICIAL")
_DESC_INDEX = 1
_GAP = 2

# Below this many columns the description is too narrow to say anything,
# so the whole table gives way to the stacked form.
_MIN_DESC = 20

# What the OFFICIAL column holds for a Docker-official image. Same mark
# `docker search` prints.
_OFFICIAL_MARK = "[OK]"


def command_search(args) -> None:
    """Implements `proot-distro search`."""
    query = (getattr(args, "query", None) or "").strip()
    quiet = bool(getattr(args, "quiet", False))

    # A missing positional is caught by the CLI (which renders help);
    # an empty or blank one reaches here and would search for nothing.
    if not query:
        crit_error("the search query is empty.")
        sys.exit(1)

    limit = _parse_limit(getattr(args, "limit", None))

    # The CLI already turns the global quiet flag on for this command
    # (which is what silences the helper's retry notices); checking it
    # here too keeps the name-per-line output clean for any caller that
    # did not, the same way push does.
    if not quiet:
        log_info(f"Searching Docker Hub for '{quote_path(query)}'...")

    try:
        results, total = search_images(query, limit)
    except KeyboardInterrupt:
        log_error("Aborted by user.")
        sys.exit(1)
    except (urllib.error.URLError, OSError) as exc:
        log_error(f"Network error: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        log_error(f"Error: {exc}")
        sys.exit(1)

    if quiet:
        for hit in results:
            print(hit["name"])
        return

    msg()
    if not results:
        msg(f"{C['YELLOW']}No images found matching "
            f"'{quote_path(query)}'.{C['RST']}")
        msg()
        return

    _render(results, total)


def _parse_limit(raw) -> int:
    """Validate the --limit value, or exit with a message naming it."""
    if raw is None:
        return SEARCH_DEFAULT_LIMIT
    try:
        limit = int(str(raw).strip())
    except ValueError:
        crit_error(
            f"'--limit' expects a whole number, "
            f"not '{quote_path(str(raw))}'."
        )
        sys.exit(1)
    if limit < 1:
        crit_error("'--limit' must be at least 1.")
        sys.exit(1)
    if limit > SEARCH_LIMIT_MAX:
        crit_error(
            f"'--limit' cannot exceed {SEARCH_LIMIT_MAX}. Docker Hub "
            f"serves {SEARCH_PAGE_MAX} results per request, so every "
            f"further {SEARCH_PAGE_MAX} costs one more request."
        )
        sys.exit(1)
    return limit


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _render(results: list, total: int) -> None:
    """Print the results table (or its stacked form) plus the footer."""
    rows = [
        (
            hit["name"],
            hit["description"],
            str(hit["stars"]),
            _fmt_count(hit["pulls"]),
            _OFFICIAL_MARK if hit["official"] else "",
        )
        for hit in results
    ]

    msg(f"{C['CYAN']}Images on Docker Hub:{C['RST']}")
    msg()

    width = terminal_width()
    widths = [
        max(len(_HEADERS[i]), max(len(r[i]) for r in rows))
        for i in range(len(_HEADERS))
    ]
    # Everything except DESCRIPTION is content-sized; DESCRIPTION gets
    # the remainder of the terminal, exactly like ps's COMMAND column.
    fixed = sum(w for i, w in enumerate(widths) if i != _DESC_INDEX)
    widths[_DESC_INDEX] = width - fixed - _GAP * (len(_HEADERS) - 1)

    if widths[_DESC_INDEX] >= _MIN_DESC:
        _render_table(rows, widths)
    else:
        _render_stacked(rows, width)

    msg()
    if total > len(rows):
        msg(f"{C['CYAN']}Showing {len(rows)} of {total} matches. "
            f"Narrow the query or raise "
            f"{C['GREEN']}--limit N{C['CYAN']}.{C['RST']}")
    msg(f"{C['CYAN']}Install with: "
        f"{C['GREEN']}{PROGRAM_NAME} install <image>{C['RST']}")
    msg()


def _render_table(rows: list, widths: list) -> None:
    """Print the hits as aligned columns."""
    pad = " " * _GAP
    head = pad.join([
        _HEADERS[0].ljust(widths[0]),
        _HEADERS[1].ljust(widths[1]),
        _HEADERS[2].rjust(widths[2]),
        _HEADERS[3].rjust(widths[3]),
        _HEADERS[4],
    ])
    msg(f"{C['UBCYAN']}{head}{C['RST']}")

    for name, desc, stars, pulls, official in rows:
        cells = [
            f"{C['GREEN']}{name.ljust(widths[0])}{C['RST']}",
            f"{C['CYAN']}{_fit(desc, widths[1]).ljust(widths[1])}{C['RST']}",
            f"{C['CYAN']}{stars.rjust(widths[2])}{C['RST']}",
            f"{C['CYAN']}{pulls.rjust(widths[3])}{C['RST']}",
        ]
        line = pad.join(cells)
        # The trailing column is only padded when it holds something,
        # so a non-official image's row does not end in blanks.
        if official:
            line += f"{pad}{C['YELLOW']}{official}{C['RST']}"
        msg(line)


def _render_stacked(rows: list, width: int) -> None:
    """Print one hit per block — for terminals too narrow to align."""
    avail = max(_MIN_DESC, width - 4)
    for i, (name, desc, stars, pulls, official) in enumerate(rows):
        if i:
            msg()
        mark = f" {C['YELLOW']}{official}{C['RST']}" if official else ""
        msg(f"  {C['CYAN']}* {C['GREEN']}{name}{C['RST']}{mark}")
        msg(f"    {C['CYAN']}{_plural(stars, 'star')}, "
            f"{_plural(pulls, 'pull')}{C['RST']}")
        if desc:
            msg(f"    {C['CYAN']}{_fit(desc, avail)}{C['RST']}")


def _plural(count: str, noun: str) -> str:
    """Render an already-formatted count with its noun ('1 star')."""
    return f"{count} {noun}" if count == "1" else f"{count} {noun}s"


def _fit(text: str, width: int) -> str:
    """Truncate *text* to *width* columns, marking the cut."""
    if len(text) <= width:
        return text
    return text[: max(1, width - 1)] + "…"


# Thresholds for _fmt_count, coarsest first. Pull counts are decimal
# quantities, not byte sizes, so the units are powers of ten.
_COUNT_UNITS = (
    (1000000000, "B"),
    (1000000, "M"),
    (1000, "K"),
)


def _fmt_count(number: int) -> str:
    """Render a pull count compactly (e.g. 13253167718 -> '13.3B')."""
    for scale, suffix in _COUNT_UNITS:
        if number >= scale:
            return f"{number / scale:.1f}{suffix}"
    return str(number)


__all__ = ("command_search",)
