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

"""List installed containers with the smallest practical API example.

This sample only reads local PRoot-Distro state. It does not download images
or change containers. Run it without arguments::

    python samples/list_containers.py

Each item returned by ``list_containers()`` is a ``ContainerInfo`` record.
Applications should read fields from these records instead of parsing output
from the ``proot-distro`` command-line utility.
"""

from pathlib import Path
import sys

# When this file is run from a source checkout, import the package from that
# checkout. This block is unnecessary when proot-distro is already installed.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proot_distro.api import ProotDistro, ProotDistroError


def main() -> int:
    """Print every installed container and return a shell status code."""
    try:
        # ProotDistro() uses the same runtime and cache directories as the CLI.
        api = ProotDistro()
        containers = api.list_containers()
    except ProotDistroError as exc:
        # One base exception catches all documented API failures. The
        # operation field identifies which API call reported the error.
        operation = exc.operation or "list_containers"
        print(f"{operation} failed: {exc}")
        return 1

    if not containers:
        print("No containers are installed.")
        return 0

    print("Installed containers:")
    for container in containers:
        # Metadata can be absent for containers restored from older backups or
        # installed from a plain root filesystem archive.
        source = container.image_reference or "local archive"
        architecture = container.architecture or "unknown architecture"
        print(f"- {container.name}: {source} ({architecture})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
