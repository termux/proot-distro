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
# Architecture: Two inventories behind one command.
#
#   default    — installed containers: subdirectories of CONTAINERS_DIR
#                that contain a rootfs/. Rendered as a bullet list.
#   --image    — cached OCI images: immutable shared inventory records for
#                manifest-cache entries plus their layer blobs.
#                Rendered as an IMAGE/ARCH/ID/SIZE/CREATED table that
#                degrades to a stacked form when the terminal is too
#                narrow to hold the columns (Termux on a phone).
#
# --quiet prints bare identifiers to stdout in both modes — container
# names, or image references for piping into `remove --image`.

import calendar
import re
import time

from proot_distro.constants import PROGRAM_NAME
from proot_distro.message import C, msg, quote_path, terminal_width
from proot_distro.operations import list_containers, list_images
from proot_distro.progress import fmt_size

# Fixed columns of the `--image` table.
_IMAGE_HEADERS = ("IMAGE", "ARCH", "ID", "SIZE", "CREATED")
_GAP = 2

# Leading date-time of an RFC 3339 timestamp (the image config's
# `created` field), with an optional fractional part and zone offset.
_TIMESTAMP_RE = re.compile(
    r"^(\d{4})-(\d{2})-(\d{2})[Tt ](\d{2}):(\d{2}):(\d{2})"
    r"(?:\.\d+)?(?:[Zz]|([+-])(\d{2}):?(\d{2}))?"
)


def command_list(args) -> None:
    """List installed containers, or cached images with --image."""
    quiet = getattr(args, "quiet", False)

    if getattr(args, "image", False):
        _list_images(quiet)
    else:
        _list_containers(quiet)


# ---------------------------------------------------------------------------
# Containers
# ---------------------------------------------------------------------------

def _list_containers(quiet: bool) -> None:
    """List every container directory that contains a rootfs/.

    The shared inventory decides both halves of that question through the
    descriptor-scoped helpers in paths.py.
    """
    entries = list_containers()

    if quiet:
        for container in entries:
            print(container.name)
        return

    msg()
    if not entries:
        msg(f"{C['YELLOW']}No containers are installed.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Install one with: "
            f"{C['GREEN']}{PROGRAM_NAME} install ubuntu:24.04{C['RST']}")
    else:
        msg(f"{C['CYAN']}Installed containers:{C['RST']}")
        msg()
        for container in entries:
            msg(f"  {C['CYAN']}* {C['GREEN']}{container.name}{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Log in with: "
            f"{C['GREEN']}{PROGRAM_NAME} login <name>{C['RST']}")
    msg()


# ---------------------------------------------------------------------------
# Images
# ---------------------------------------------------------------------------

def _list_images(quiet: bool) -> None:
    """List every image held in the local manifest + layer cache."""
    images = list_images()

    if quiet:
        seen = set()
        for image in images:
            name = image_identifier(image)
            if name in seen:
                continue
            seen.add(name)
            # The reference is a manifest-cache entry's text, which is
            # guest-writable on Termux: a lone surrogate in it (a JSON
            # `\\udcff`) raised UnicodeEncodeError out of strict stdout.
            print(quote_path(name))
        return

    msg()
    if not images:
        msg(f"{C['YELLOW']}No images are cached.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}Download one with: "
            f"{C['GREEN']}{PROGRAM_NAME} install ubuntu:24.04{C['RST']}")
        msg()
        return

    now = time.time()
    any_incomplete = any(image.missing_layers for image in images)
    # The first three cells are a manifest-cache entry's own text -- its
    # reference, its architecture, its config digest or else its file
    # name -- and that directory is guest-writable on Termux, so each is
    # quoted before it is measured or printed: an entry saying ESC put
    # the escape sequence on the user's terminal as it stood.
    rows = [
        (
            quote_path(image_display_ref(image))
            + ("*" if image.missing_layers else ""),
            quote_path(image.architecture or "?"),
            quote_path(image.cache_image_id[:12] or image.cache_key),
            fmt_size(image.size_bytes),
            _fmt_created(image, now),
        )
        for image in images
    ]

    msg(f"{C['CYAN']}Cached images:{C['RST']}")
    msg()

    widths = [
        max(len(_IMAGE_HEADERS[i]), max(len(r[i]) for r in rows))
        for i in range(len(_IMAGE_HEADERS))
    ]
    if sum(widths) + _GAP * (len(widths) - 1) <= terminal_width():
        _render_table(rows, widths)
    else:
        _render_stacked(rows)

    if any_incomplete:
        msg()
        msg(f"{C['YELLOW']}* incomplete - some layers are missing from "
            f"the cache{C['RST']}")
    msg()
    msg(f"{C['CYAN']}Install with: "
        f"{C['GREEN']}{PROGRAM_NAME} install <image>{C['RST']}")
    msg(f"{C['CYAN']}Remove with:  "
        f"{C['GREEN']}{PROGRAM_NAME} remove --image <image>{C['RST']}")
    msg()


def _render_table(rows: list, widths: list) -> None:
    """Print the image rows as aligned columns."""
    pad = " " * _GAP
    head = [
        _IMAGE_HEADERS[i].ljust(widths[i]) for i in range(len(widths) - 1)
    ]
    head.append(_IMAGE_HEADERS[-1])
    msg(f"{C['UBCYAN']}{pad.join(head)}{C['RST']}")

    for row in rows:
        cells = [f"{C['GREEN']}{row[0].ljust(widths[0])}{C['RST']}"]
        cells += [
            f"{C['CYAN']}{row[i].ljust(widths[i])}{C['RST']}"
            for i in range(1, len(widths) - 1)
        ]
        cells.append(f"{C['CYAN']}{row[-1]}{C['RST']}")
        msg(pad.join(cells))


def _render_stacked(rows: list) -> None:
    """Print one image per two lines — for terminals too narrow to align."""
    for i, row in enumerate(rows):
        if i:
            msg()
        msg(f"  {C['CYAN']}* {C['GREEN']}{row[0]}{C['RST']}")
        msg(f"    {C['CYAN']}{', '.join(row[1:])}{C['RST']}")


def image_display_ref(image) -> str:
    """Return the reference to show for *image*.

    Entries whose reference could not be recovered (written by an older
    version, with no installed container left to identify them) show the
    repository they were pulled from and a `<none>` tag, mirroring how
    Docker labels an image it can no longer name.
    """
    if image.reference:
        return image.reference
    return (
        f"{image.repository}:<none>"
        if image.repository else "<none>:<none>"
    )


def image_identifier(image) -> str:
    """Return the token that addresses *image* on the command line."""
    return image.identifier


def _fmt_created(image, now: float) -> str:
    """Render the CREATED cell for *image* relative to *now*."""
    epoch = _created_epoch(image)
    return _fmt_age(now - epoch) if epoch > 0 else "unknown"


def _created_epoch(image) -> float:
    """Return the image's creation time in epoch seconds.

    Falls back to the mtime of the manifest-cache entry — i.e. when the
    image was pulled or built — for images whose config carries no
    `created` field, which is the norm for locally built ones.
    """
    match = _TIMESTAMP_RE.match(image.created or "")
    if match:
        sign, off_h, off_m = match.group(7), match.group(8), match.group(9)
        try:
            epoch = calendar.timegm(
                tuple(int(g) for g in match.groups()[:6]) + (0, 0, 0)
            )
            if sign:
                offset = int(off_h) * 3600 + int(off_m) * 60
                epoch += -offset if sign == "+" else offset
            return float(epoch)
        except (ValueError, OverflowError):
            pass
    return float(image.cached_at or 0.0)


# Unit thresholds for _fmt_age, coarsest first.
_AGE_UNITS = (
    ("year", 31536000),
    ("month", 2592000),
    ("week", 604800),
    ("day", 86400),
    ("hour", 3600),
    ("minute", 60),
)


def _fmt_age(seconds: float) -> str:
    """Render an elapsed time as 'N units ago' (e.g. '3 weeks ago')."""
    total = int(seconds)
    for unit, unit_seconds in _AGE_UNITS:
        if total >= unit_seconds:
            count = total // unit_seconds
            return f"{count} {unit}{'s' if count > 1 else ''} ago"
    return "just now"


__all__ = ("command_list",)
