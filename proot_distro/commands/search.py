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

# Architecture: `proot-distro search TERM`, the analogue of `docker
# search`. Queries Docker Hub through helpers/docker/search.py and
# renders a NAME/DESCRIPTION/STARS/OFFICIAL/AUTOMATED/ARCH table that
# degrades to a stacked form on narrow terminals, mirroring the
# `list --image` layout. The ARCH column is filled by querying each
# repository's multi-arch manifest index concurrently; images whose
# 'latest' tag cannot be resolved show '?'. `--quiet` prints bare
# repository names for piping into `install`.

import sys
import urllib.error
from concurrent.futures import ThreadPoolExecutor

from proot_distro.constants import PROGRAM_NAME
from proot_distro.message import (
    C, msg, log_info, log_error, crit_error, terminal_width,
)
from proot_distro.helpers.docker import image_architectures, search_images

# Fixed column order of the results table (docker search columns plus ARCH).
_HEADERS = ("NAME", "DESCRIPTION", "STARS", "OFFICIAL", "AUTOMATED", "ARCH")
_GAP = 2
# DESCRIPTION column must keep at least this many columns before the
# table degrades to the stacked form (Termux on a phone).
_STACKED_MIN = 24
# Concurrent manifest-index queries per search (capped at the result count).
_ARCH_WORKERS = 8


def command_search(args) -> None:
    """Implements `proot-distro search`."""
    query = getattr(args, "query", None) or ""
    if not query:
        crit_error("search term is not specified.")
        sys.exit(1)
    limit = getattr(args, "limit", 25) or 25
    quiet = bool(getattr(args, "quiet", False))

    try:
        results = search_images(query, limit)
    except KeyboardInterrupt:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        log_error("Aborted by user.")
        sys.exit(1)
    except (urllib.error.URLError, OSError) as exc:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        log_error(f"Network error: {exc}")
        sys.exit(1)
    except RuntimeError as exc:
        if sys.stderr.isatty():
            sys.stderr.write("\r\033[K")
            sys.stderr.flush()
        log_error(f"Error: {exc}")
        sys.exit(1)

    if quiet:
        for result in results:
            print(result.get("name", "?"))
        return

    if results:
        log_info(f"Resolving architectures for {len(results)} image(s)...")
        _resolve_architectures(results)

    msg()
    if not results:
        msg(f"{C['YELLOW']}No results found for '{query}' on Docker Hub."
            f"{C['RST']}")
        msg()
        return

    msg(f"{C['CYAN']}Search results for '{query}' on Docker Hub:{C['RST']}")
    msg()
    _render_results(results)
    msg()
    msg(f"{C['CYAN']}Install with: "
        f"{C['GREEN']}{PROGRAM_NAME} install <image>{C['RST']}")
    msg()


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _resolve_architectures(results: list) -> None:
    """Populate each result's 'architectures' from the registry manifest.

    Every repository is queried for its multi-arch manifest index. The
    queries run concurrently so a full page of results does not serialize
    into dozens of sequential round-trips. A repository that cannot be
    resolved gets an empty list — the table shows '?' for it instead of
    failing the whole search.
    """
    names = [result.get("name") or "?" for result in results]
    workers = min(_ARCH_WORKERS, len(names))
    with ThreadPoolExecutor(max_workers=workers) as pool:
        archs = list(pool.map(image_architectures, names))
    for result, arch in zip(results, archs):
        result["architectures"] = arch


def _row(result: dict) -> tuple:
    """Turn one API result into the (name, desc, stars, official, automated,
    arch) row tuple shared by both table and stacked renderers."""
    name = result.get("name", "?")
    desc = (result.get("description") or "").replace("\n", " ").strip()
    stars = result.get("star_count") or 0
    official = "[OK]" if result.get("is_official") else ""
    automated = "[OK]" if result.get("is_automated") else ""
    arch = "/".join(result.get("architectures") or []) or "?"
    return name, desc, str(stars), official, automated, arch


def _render_results(results: list) -> None:
    rows = [_row(r) for r in results]
    widths = [
        max(len(_HEADERS[i]), max(len(r[i]) for r in rows))
        for i in range(len(_HEADERS))
    ]
    # NAME/STARS/OFFICIAL/AUTOMATED/ARCH are fixed columns; DESCRIPTION
    # flexes to consume whatever width the terminal leaves over.
    fixed = (
        widths[0] + widths[2] + widths[3] + widths[4] + widths[5]
        + _GAP * (len(widths) - 1)
    )
    desc_limit = terminal_width() - fixed
    if desc_limit < _STACKED_MIN:
        _render_stacked(rows)
        return
    widths[1] = min(widths[1], desc_limit)

    pad = " " * _GAP
    head = [_HEADERS[i].ljust(widths[i]) for i in range(len(widths) - 1)]
    head.append(_HEADERS[-1])
    msg(f"{C['UBCYAN']}{pad.join(head)}{C['RST']}")

    for row in rows:
        cells = [
            f"{C['GREEN']}{_ellipsize(row[0], widths[0]).ljust(widths[0])}"
            f"{C['RST']}",
            f"{C['CYAN']}{_ellipsize(row[1], widths[1]).ljust(widths[1])}"
            f"{C['RST']}",
            f"{C['CYAN']}{row[2].rjust(widths[2])}{C['RST']}",
            f"{C['CYAN']}{row[3].ljust(widths[3])}{C['RST']}",
            f"{C['CYAN']}{row[4].ljust(widths[4])}{C['RST']}",
            f"{C['CYAN']}{row[5]}{C['RST']}",
        ]
        msg(pad.join(cells))


def _render_stacked(rows: list) -> None:
    """Print one result per two lines — for terminals too narrow to align."""
    for i, row in enumerate(rows):
        if i:
            msg()
        msg(f"  {C['CYAN']}* {C['GREEN']}{row[0]}{C['RST']}")
        detail = [row[1]]
        flags = " ".join(f for f in (row[3], row[4]) if f)
        if flags:
            detail.append(flags)
        detail.append(f"{row[2]} stars")
        detail.append(f"arch: {row[5]}")
        msg(f"    {C['CYAN']}{', '.join(d for d in detail if d)}{C['RST']}")


def _ellipsize(text: str, width: int) -> str:
    """Truncate *text* to *width* columns, appending an ellipsis when cut."""
    if len(text) <= width:
        return text
    if width <= 1:
        return text[:width]
    return text[: width - 1] + "…"


__all__ = ("command_search",)
