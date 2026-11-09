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

"""Build commands for private Python workers from the imported package.

Workers can be started while an application is inside an untrusted build
directory or has Python startup hooks configured. ``-I -S`` excludes the
working directory, environment-controlled Python paths, user site packages,
and ``sitecustomize``/``usercustomize``. The tiny standard-library bootstrap
then places this package's actual parent first and executes the requested
private module.
"""

import os
import sys


_BOOTSTRAP = (
    "import runpy,sys;"
    "sys.path.insert(0,sys.argv.pop(1));"
    "runpy.run_module(sys.argv.pop(1),run_name='__main__')"
)


def private_module_command(module: str) -> list:
    """Return an isolated command executing *module* from this package."""
    if not isinstance(module, str) or not module.startswith("proot_distro."):
        raise ValueError("private worker module is not valid")
    package_parent = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [
        sys.executable,
        "-I",
        "-S",
        "-c",
        _BOOTSTRAP,
        package_parent,
        module,
    ]


__all__ = ()
