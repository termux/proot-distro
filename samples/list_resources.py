#!/usr/bin/env python3
#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
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

"""List resources through the basic PRoot-Distro Python API.

This is the safest sample to run first. Inventory calls only read local
PRoot-Distro state. Supplying ``--search`` additionally performs a public
Docker Hub search.

Examples::

    python samples/list_resources.py
    python samples/list_resources.py --search alpine --limit 5

The records returned by the API are immutable dataclasses. Applications should
use their fields, rather than parsing the human-readable output of the CLI.
All documented API failures derive from ``ProotDistroError`` and include the
operation name that failed.
"""

import argparse
from pathlib import Path
import sys
from typing import Optional, Sequence

# Make direct execution from a source checkout use that exact checkout. An
# installed copy needs no adjustment because these samples are repository files.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proot_distro.api import ProotDistro, ProotDistroError


def _size(value: int) -> str:
    """Format a byte count without requiring a third-party dependency."""
    amount = float(value)
    for unit in ("B", "KiB", "MiB", "GiB", "TiB"):
        if amount < 1024 or unit == "TiB":
            return f"{amount:.0f} {unit}" if unit == "B" else f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--search",
        metavar="QUERY",
        help="also search public Docker Hub repositories",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=5,
        help="maximum search results to display (default: 5)",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Print local inventories and return a process-style status code."""
    args = _parser().parse_args(argv)

    try:
        pd = ProotDistro()
        containers = pd.list_containers()
        images = pd.list_images()
        sessions = pd.list_sessions()

        print(f"Containers ({len(containers)}):")
        for container in containers:
            origin = container.image_reference or "local archive"
            architecture = container.architecture or "unknown architecture"
            print(f"  {container.name}: {origin} [{architecture}]")
        if not containers:
            print("  (none)")

        print(f"\nCached images ({len(images)}):")
        for image in images:
            state = "complete" if image.complete else "incomplete"
            reference = image.reference or image.identifier
            print(
                f"  {reference}: {_size(image.size_bytes)}, "
                f"{image.layer_count} layers, {state}"
            )
        if not images:
            print("  (none)")

        print(f"\nLive sessions ({len(sessions)}):")
        for session in sessions:
            command = " ".join(session.command) or "(interactive)"
            mode = "detached" if session.detached else "foreground"
            print(
                f"  pid {session.pid}: {session.container} "
                f"[{session.kind}, {mode}] {command}"
            )
        if not sessions:
            print("  (none)")

        if args.search is not None:
            result = pd.search(args.search, limit=args.limit)
            print(f"\nDocker Hub results ({result.total} total):")
            for hit in result.hits:
                marker = " [official]" if hit.official else ""
                print(f"  {hit.name}{marker}: {hit.description}")
            if not result.hits:
                print("  (none)")
    except ProotDistroError as exc:
        operation = exc.operation or "operation"
        print(f"{operation} failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
