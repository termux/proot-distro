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

"""Install one container from an OCI image.

This beginner sample demonstrates one state-changing API call. For example::

    python samples/install_container.py alpine:latest beginner-alpine

The first argument is an image reference. The second is the new local
container name. The operation downloads data when it is not already cached.
It refuses to replace an existing container.

The new container is stored in the same location used by the PRoot-Distro
command-line utility. You can inspect it afterward with::

    proot-distro list
"""

import argparse
from pathlib import Path
import sys
from typing import Optional, Sequence

# Prefer the current checkout during direct execution. Installed copies of the
# package do not need this path adjustment.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proot_distro.api import ProotDistro, ProotDistroError


def _parser() -> argparse.ArgumentParser:
    """Build the small command-line interface used by this sample."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("image", help="OCI image reference, such as alpine:latest")
    parser.add_argument("name", help="name for the new local container")
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Install the requested image and report the resulting metadata."""
    args = _parser().parse_args(argv)

    try:
        # The facade shares state with the CLI when no RuntimeConfig is passed.
        api = ProotDistro()

        # install() returns ContainerInfo; there is no need to query the
        # inventory again or parse command-line output.
        container = api.install(args.image, name=args.name)
    except ProotDistroError as exc:
        operation = exc.operation or "install"
        print(f"{operation} failed: {exc}")
        return 1

    source = container.image_reference or args.image
    architecture = container.architecture or "unknown architecture"
    print(f"Installed {container.name} from {source} ({architecture}).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
