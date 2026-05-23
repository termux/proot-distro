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

# Architecture: One context manager for the "write to a sibling .tmp
# file, then atomically rename" pattern that lives in every cache /
# layer / manifest writer in the codebase. Centralising it gives us a
# single place to:
#
#   - resolve the destination directory and create it if missing,
#   - mint a process-unique tmp filename so concurrent writers cannot
#     race on a shared <dest>.tmp,
#   - guarantee the tmp file is removed when the with-block exits
#     unsuccessfully (including KeyboardInterrupt, so a Ctrl-C never
#     leaves a half-written sentinel behind).

import contextlib
import os
import tempfile


@contextlib.contextmanager
def atomic_replace(path: str, *, suffix: str = ".tmp"):
    """Yield a tmp path next to *path*; rename on success, remove on error.

    The caller writes to the yielded tmp path however it pleases —
    open()/tarfile.open()/shutil.copyfileobj are all fine. On normal
    exit the tmp is os.replace()'d onto *path* (atomic on POSIX). On
    any exception the tmp is removed and the original exception
    re-raised.

    A unique tmp name is minted per call so two concurrent writers to
    the same final path (e.g. two `build`s sharing a base image)
    cannot collide on a sentinel.
    """
    dest_dir = os.path.dirname(path) or "."
    os.makedirs(dest_dir, exist_ok=True)
    fd, tmp = tempfile.mkstemp(
        prefix=os.path.basename(path) + ".",
        suffix=suffix,
        dir=dest_dir,
    )
    os.close(fd)
    try:
        yield tmp
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise
