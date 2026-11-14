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

"""Run one foreground command in an existing container.

Install a container first, then provide its name and a command::

    python samples/run_command.py beginner-alpine uname -a

Place ``--`` before a command option that should not be interpreted by this
sample::

    python samples/run_command.py beginner-alpine -- ls -la /

If no command is supplied, the sample runs ``uname -a``. Standard input,
standard output, and standard error remain connected to the current terminal.
"""

import argparse
from pathlib import Path
import sys
from typing import List, Optional, Sequence

# Import from this source checkout when the sample is executed directly.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proot_distro.api import ProotDistro, ProotDistroError


def _parser() -> argparse.ArgumentParser:
    """Build the command-line interface used by the example."""
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("container", help="name of an installed container")
    parser.add_argument(
        "command",
        nargs=argparse.REMAINDER,
        help="guest command and its arguments (default: uname -a)",
    )
    return parser


def _command(values: Sequence[str]) -> List[str]:
    """Remove argparse's optional separator and provide a useful default."""
    command = list(values)
    if command and command[0] == "--":
        command.pop(0)
    return command or ["uname", "-a"]


def main(argv: Optional[Sequence[str]] = None) -> int:
    """Start the command, wait for it, and return a simple status code."""
    args = _parser().parse_args(argv)
    command = _command(args.command)

    try:
        api = ProotDistro()

        # start() creates a login-style session. With no stdout or stderr
        # arguments, the child inherits this script's terminal streams.
        #
        # SessionProcess is a context manager. Leaving the block terminates and
        # reaps the tracked process tree if waiting raises an exception.
        with api.start(args.container, command) as process:
            returncode = process.wait()
    except ProotDistroError as exc:
        operation = exc.operation or "start"
        print(f"{operation} failed: {exc}")
        return 1
    except KeyboardInterrupt:
        print("Interrupted after PRoot-Distro cleanup.")
        return 130

    if returncode != 0:
        print(f"Guest command exited with status {returncode}.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
