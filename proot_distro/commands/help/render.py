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

# Architecture: Layout primitives for the help renderer. The page data
# is a plain Python dict (see pages.py); this module is pure formatting
# — wraps paragraphs, renders the [usage]/[options]/[examples] sections,
# and switches between a stacked-vertical layout on narrow PTYs and a
# two-column layout on wider screens.

import os
import shutil
import textwrap

from proot_distro.constants import (
    PROGRAM_NAME, CANONICAL_PROGRAM_NAME, PROGRAM_AUTHOR, PROGRAM_VERSION,
)
from proot_distro.message import C, msg


# Help text is wrapped to fit comfortably on both narrow phone PTYs and
# wide laptop terminals.
_MIN_WIDTH = 32
_MAX_WIDTH = 92

# Below this column count, option name + description stack vertically.
NARROW_BREAKPOINT = 60

_RULE_SINGLE = "─"
_BULLET = "▸"
_PROMPT = "$"


def term_width() -> int:
    """Return a sensible terminal width clamped to [_MIN_WIDTH, _MAX_WIDTH]."""
    # Help text is written to stderr, so prefer stderr's reported size
    # first and fall back to stdout/stdin and finally COLUMNS for
    # redirected runs.
    for fd in (2, 1, 0):
        try:
            cols = os.get_terminal_size(fd).columns
        except (OSError, ValueError):
            continue
        if cols > 0:
            return max(_MIN_WIDTH, min(_MAX_WIDTH, cols))
    try:
        cols = int(os.environ.get("COLUMNS", "0"))
    except ValueError:
        cols = 0
    if cols > 0:
        return max(_MIN_WIDTH, min(_MAX_WIDTH, cols))
    try:
        cols = shutil.get_terminal_size((72, 24)).columns
    except (OSError, ValueError):
        cols = 72
    return max(_MIN_WIDTH, min(_MAX_WIDTH, cols))


def _wrap(text: str, width: int) -> list:
    width = max(4, width)
    lines = textwrap.wrap(
        text, width=width,
        break_long_words=False, break_on_hyphens=False,
    )
    return lines or [""]


# ----- low-level pieces ----------------------------------------------------

def _hrule(width: int, char: str, color: str = None) -> str:
    return f"{color or C['CYAN']}{char * width}{C['RST']}"


def section(label: str) -> None:
    msg()
    msg(f"{C['UBCYAN']}{label}{C['RST']}")
    msg()


def paragraph(text: str, width: int, indent: int = 2,
              color: str = None) -> None:
    color = color or C["CYAN"]
    pad = " " * indent
    avail = max(8, width - indent)
    for i, para in enumerate(text.split("\n\n")):
        if i > 0:
            msg()
        for line in _wrap(para, avail):
            msg(f"{pad}{color}{line}{C['RST']}")


def usage_line(usage: str, width: int) -> None:
    parts = usage.split(" ", 1)
    sub = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    head = (f"  {C['BGREEN']}{PROGRAM_NAME}{C['RST']}"
            f" {C['UGREEN']}{sub}{C['RST']}")
    head_visible = 2 + len(PROGRAM_NAME) + 1 + len(sub)
    if not rest:
        msg(head)
        return
    if head_visible + 1 + len(rest) <= width:
        msg(f"{head} {C['CYAN']}{rest}{C['RST']}")
        return
    msg(head)
    cont = "    "
    for line in _wrap(rest, width - len(cont)):
        msg(f"{cont}{C['CYAN']}{line}{C['RST']}")


def aliases_block(aliases) -> None:
    sep = f"{C['CYAN']}, {C['RST']}"
    parts = [f"{C['UGREEN']}{a}{C['RST']}" for a in aliases]
    msg(f"  {C['CYAN']}Aliases:{C['RST']} {sep.join(parts)}")


def _options_stacked(options, width: int) -> None:
    last = len(options) - 1
    for i, (name, desc) in enumerate(options):
        msg(f"  {C['GREEN']}{name}{C['RST']}")
        paragraph(desc, width, indent=4)
        if i != last:
            msg()


def options_block(options, width: int) -> None:
    if not options:
        return
    if width < NARROW_BREAKPOINT:
        _options_stacked(options, width)
        return
    longest = max(len(name) for name, _ in options)
    opt_col = min(longest, max(16, width // 3))
    desc_col = width - opt_col - 4
    if desc_col < 24:
        _options_stacked(options, width)
        return
    cont = " " * (2 + opt_col + 2)
    last = len(options) - 1
    for i, (name, desc) in enumerate(options):
        wrapped = _wrap(desc, desc_col)
        if len(name) <= opt_col:
            head = (
                f"  {C['GREEN']}{name}{C['RST']}"
                f"{' ' * (opt_col - len(name))}  "
                f"{C['CYAN']}{wrapped[0]}{C['RST']}"
            )
            msg(head)
            for line in wrapped[1:]:
                msg(f"{cont}{C['CYAN']}{line}{C['RST']}")
        else:
            msg(f"  {C['GREEN']}{name}{C['RST']}")
            for line in wrapped:
                msg(f"{cont}{C['CYAN']}{line}{C['RST']}")
        if i != last:
            msg()


def commands_block(commands, width: int) -> None:
    """Like options_block, but each entry may carry a trailing red warning.

    Each entry is (name, description) or (name, description, warning).
    """
    if not commands:
        return
    longest = max(len(entry[0]) for entry in commands)
    name_col = min(longest, max(12, width // 4))
    desc_col = width - name_col - 4
    narrow = width < NARROW_BREAKPOINT or desc_col < 24

    if narrow:
        last = len(commands) - 1
        for i, entry in enumerate(commands):
            name = entry[0]
            desc = entry[1]
            warn = entry[2] if len(entry) > 2 else None
            msg(f"  {C['GREEN']}{name}{C['RST']}")
            paragraph(desc, width, indent=4)
            if warn:
                msg(f"    {C['RED']}{warn}{C['RST']}")
            if i != last:
                msg()
        return

    cont = " " * (2 + name_col + 2)
    for entry in commands:
        name = entry[0]
        desc = entry[1]
        warn = entry[2] if len(entry) > 2 else None
        wrapped = _wrap(desc, desc_col)
        head = (
            f"  {C['GREEN']}{name}{C['RST']}"
            f"{' ' * (name_col - len(name))}  "
            f"{C['CYAN']}{wrapped[0]}{C['RST']}"
        )
        if warn and len(wrapped) == 1 and \
                len(wrapped[0]) + 1 + len(warn) <= desc_col:
            msg(f"{head} {C['RED']}{warn}{C['RST']}")
            continue
        msg(head)
        for line in wrapped[1:]:
            msg(f"{cont}{C['CYAN']}{line}{C['RST']}")
        if warn:
            msg(f"{cont}{C['RED']}{warn}{C['RST']}")


def shell_block(examples, width: int) -> None:
    avail = max(12, width - 4)
    wrap_avail = max(4, avail - 2)
    for ex in examples:
        wrapped = _wrap(ex, wrap_avail)
        last = len(wrapped) - 1
        for i, line in enumerate(wrapped):
            suffix = "" if i == last else f" {C['CYAN']}\\{C['RST']}"
            if i == 0:
                msg(
                    f"  {C['YELLOW']}{_PROMPT}{C['RST']} "
                    f"{C['GREEN']}{line}{C['RST']}{suffix}"
                )
            else:
                msg(f"    {C['GREEN']}{line}{C['RST']}{suffix}")


def bullets_block(bullets, width: int) -> None:
    if not bullets:
        return
    longest = max(len(label) for label, _ in bullets)
    name_col = min(longest, max(16, width // 3))
    desc_col = width - 4 - name_col - 2
    narrow = width < NARROW_BREAKPOINT or desc_col < 16

    if narrow:
        for label, comment in bullets:
            msg(
                f"  {C['CYAN']}{_BULLET}{C['RST']} "
                f"{C['YELLOW']}{label}{C['RST']}"
            )
            if comment:
                paragraph(f"({comment})", width, indent=6)
        return

    cont = " " * (4 + name_col + 2)
    for label, comment in bullets:
        pad = " " * max(0, name_col - len(label))
        if comment:
            wrapped = _wrap(f"({comment})", desc_col)
            msg(
                f"  {C['CYAN']}{_BULLET}{C['RST']} "
                f"{C['YELLOW']}{label}{C['RST']}{pad}  "
                f"{C['CYAN']}{wrapped[0]}{C['RST']}"
            )
            for line in wrapped[1:]:
                msg(f"{cont}{C['CYAN']}{line}{C['RST']}")
        else:
            msg(
                f"  {C['CYAN']}{_BULLET}{C['RST']} "
                f"{C['YELLOW']}{label}{C['RST']}"
            )


def footer(width: int) -> None:
    msg()
    msg(_hrule(width, _RULE_SINGLE, C["CYAN"]))
    paragraph(
        f"{CANONICAL_PROGRAM_NAME} version '{PROGRAM_VERSION}' by "
        f"{PROGRAM_AUTHOR}.",
        width, indent=0, color=C["ICYAN"],
    )
    msg()


# ----- one-page renderer ---------------------------------------------------

def render_page(page: dict) -> None:
    """Render a per-command help page to stderr.

    *page* is one entry from HELP_PAGES: a dict that may carry
    ``usage``, ``aliases``, ``summary``, ``options``, ``examples`` and
    a list of ``footer`` blocks (NOTES / HOST BINDINGS / ...).
    """
    width = term_width()

    if page.get("usage"):
        section("USAGE")
        usage_line(page["usage"], width)
        if page.get("aliases"):
            msg()
            aliases_block(page["aliases"])

    if page.get("summary"):
        section("DESCRIPTION")
        paragraph(page["summary"], width)

    if page.get("options"):
        section("OPTIONS")
        options_block(page["options"], width)

    if page.get("examples"):
        section("EXAMPLES")
        shell_block(page["examples"], width)

    for block in page.get("footer", []):
        title = block.get("title")
        if title:
            section(title)
        intro = block.get("intro")
        if intro:
            paragraph(intro, width)
        bullets = block.get("bullets")
        if bullets:
            if intro:
                msg()
            bullets_block(bullets, width)
        examples = block.get("examples")
        if examples:
            if intro or bullets:
                msg()
            shell_block(examples, width)

    footer(width)
