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

"""Package indexes that import a submodule only when a name needs it.

Importing ``pkg.sub`` runs ``pkg/__init__.py`` first, so an index that
re-exports every submodule eagerly makes each of them pay for all the
rest: a worker that only lists containers imported the build engine, the
registry client and the TLS stack. An index built here keeps its names
(``from pkg import name``, ``pkg.name``, ``dir(pkg)``, ``__all__``) while
importing the defining submodule on first use (PEP 562).
"""

import importlib
import sys


def lazy_exports(package: str, exports: dict):
    """Return the ``__getattr__`` and ``__dir__`` for *package*'s index.

    *exports* maps each re-exported name to the submodule, relative to
    *package*, that defines it. A name is looked up once: the value is
    then stored on the package, where normal attribute lookup finds it
    and this hook is no longer consulted. Any other name raises
    AttributeError, which is also what lets ``from pkg import sub`` fall
    through to importing the submodule ``sub``.
    """

    def __getattr__(name):
        submodule = exports.get(name)
        if submodule is None:
            raise AttributeError(
                f"module {package!r} has no attribute {name!r}"
            )
        value = getattr(
            importlib.import_module(f"{package}.{submodule}"), name
        )
        setattr(sys.modules[package], name, value)
        return value

    def __dir__():
        return sorted(set(vars(sys.modules[package])) | set(exports))

    return __getattr__, __dir__


__all__ = ("lazy_exports",)
