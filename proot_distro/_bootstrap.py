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
then appends this package's actual parent to ``sys.path`` and executes the
requested private module.

Appended, not prepended. That parent is site-packages, or whatever directory
an application vendors this package into, and everything else in it --
another distribution's ``json.py``, an application module named
``selectors`` -- would otherwise shadow the standard library inside the
worker, which no normal interpreter does. Under ``-I -S`` the only other
entries are the standard library's own, which hold no ``proot_distro``, so
appending still selects exactly the package this process imported.
"""

import os
import sys


_BOOTSTRAP = (
    "import runpy,sys;"
    "sys.path.append(sys.argv.pop(1));"
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
