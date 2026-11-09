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

"""Install a container and run a captured foreground command.

The sample demonstrates a small container lifecycle:

* install an OCI image, URL, or local rootfs archive;
* inspect the command PRoot-Distro would prepare;
* start a foreground login command with explicit environment and pipes;
* collect bytes from stdout and stderr; and
* optionally remove only the container installed by this invocation.

The default command needs a POSIX shell in the guest. Place ``--`` before a
custom command so its options are not interpreted by this script.

Examples::

    python samples/install_and_run.py alpine:3.21 sample-alpine --remove
    python samples/install_and_run.py debian:stable sample-debian -- uname -a

Installation and ``--remove`` modify the same state used by the CLI. Existing
containers are never adopted or removed: if installation reports that the
name already exists, the script exits without cleanup.
"""

import argparse
from pathlib import Path
import subprocess
import sys
from typing import List, Optional, Sequence

# Make direct execution from a source checkout use that exact checkout. An
# installed copy needs no adjustment because these samples are repository files.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proot_distro.api import ProotDistro, ProotDistroError


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("source", help="image reference, URL, or local archive")
    parser.add_argument("name", help="name for the installed container")
    parser.add_argument(
        "--architecture",
        help="requested image architecture, such as arm64 or amd64",
    )
    parser.add_argument(
        "--startup-timeout",
        type=float,
        default=30.0,
        help="maximum seconds for proot setup (default: 30)",
    )
    parser.add_argument(
        "--command-timeout",
        type=float,
        default=60.0,
        help="maximum seconds for the guest command (default: 60)",
    )
    parser.add_argument(
        "--remove",
        action="store_true",
        help="remove the newly installed container before exiting",
    )
    return parser


def _command(values: Sequence[str]) -> List[str]:
    command = list(values)
    if command and command[0] == "--":
        command.pop(0)
    if command:
        return command
    return [
        "/bin/sh",
        "-c",
        "printf 'Hello from the PRoot-Distro API!\\n'; uname -m",
    ]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Run the lifecycle while preventing raw API tracebacks."""
    raw_arguments = list(sys.argv[1:] if argv is None else argv)
    if "--" in raw_arguments:
        separator = raw_arguments.index("--")
        guest_command = raw_arguments[separator + 1:]
        del raw_arguments[separator:]
    else:
        guest_command = []
    args = _parser().parse_args(raw_arguments)
    pd = None
    installed = False
    status = 0

    try:
        pd = ProotDistro()
        container = pd.install(
            args.source,
            name=args.name,
            architecture=args.architecture,
        )
        installed = True
        print(
            f"Installed {container.name} from "
            f"{container.image_reference or args.source}."
        )

        command = _command(guest_command)
        description = pd.inspect_start(
            container.name,
            command,
            environment={"PD_API_SAMPLE": "1"},
        )
        print(f"Prepared guest command: {' '.join(description.inner_command)}")
        print(f"Working directory: {description.work_dir}")

        with pd.start(
            container.name,
            command,
            environment={"PD_API_SAMPLE": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            startup_timeout=args.startup_timeout,
        ) as process:
            stdout, stderr = process.communicate(timeout=args.command_timeout)
            returncode = process.returncode

        if stdout:
            print("stdout:")
            print(stdout.decode("utf-8", "replace"), end="")
        if stderr:
            print("stderr:")
            print(stderr.decode("utf-8", "replace"), end="")
        if returncode:
            print(f"Guest command exited with status {returncode}.")
            status = 1
    except subprocess.TimeoutExpired:
        # Leaving the SessionProcess context terminates and reaps the full
        # tracked guest process tree, even on this timeout path.
        print(f"Guest command exceeded {args.command_timeout:g} seconds.")
        status = 1
    except ProotDistroError as exc:
        operation = exc.operation or "operation"
        print(f"{operation} failed: {exc}")
        status = 1
    finally:
        if installed and args.remove and pd is not None:
            try:
                pd.remove_container(args.name)
                print(f"Removed {args.name}.")
            except ProotDistroError as exc:
                operation = exc.operation or "cleanup"
                print(f"{operation} failed during cleanup: {exc}")
                status = 1
    return status


if __name__ == "__main__":
    raise SystemExit(main())
