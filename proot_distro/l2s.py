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

# Architecture: proot's --link2symlink extension stores hard-link backing
# files under <rootfs>/.l2s/ and replaces the original locations with
# symlinks whose targets are absolute paths into <rootfs>. After we move
# the rootfs (rename, legacy-layout migration), every l2s symlink target
# still points at the previous location and the inner files appear
# broken.
#
# This module rewrites those symlinks in-place. The rewrite walks the
# whole tree, so we intercept SIGINT/SIGQUIT for the duration: if the
# user Ctrl-C's mid-walk we'd leave the container in a half-rewritten
# state where some inner files point at a now-missing path. Instead the
# signal handler prints a warning and the walk continues to completion.

import os
import signal

from proot_distro.message import log_info, log_error


def rewrite_l2s_targets(rootfs: str, old_prefix: str) -> None:
    """Rewrite every symlink in *rootfs* whose target starts with *old_prefix*.

    The new prefix is the absolute path of *rootfs* itself. Errors on
    individual symlinks (e.g. read-only fs) are swallowed so a single
    bad entry doesn't abort the rewrite.

    SIGINT and SIGQUIT are intercepted for the duration of the walk:
    aborting partway through would leave dangling symlinks that point
    at the no-longer-existing *old_prefix*. The handler emits a
    warning and the operation continues to a clean state.
    """
    log_info("Updating PRoot link2symlink extension files "
             "(may take a while)...")

    def _warn_no_interrupt(_signum, _frame):
        log_error("Terminating now will leave link2symlink symlinks broken. "
                  "Please wait for the operation to complete.")

    old_sigint = signal.signal(signal.SIGINT, _warn_no_interrupt)
    old_sigquit = signal.signal(signal.SIGQUIT, _warn_no_interrupt)
    try:
        for dirpath, _dirs, filenames in os.walk(rootfs):
            for fname in filenames:
                fpath = os.path.join(dirpath, fname)
                try:
                    if not os.path.islink(fpath):
                        continue
                    target = os.readlink(fpath)
                    if not target.startswith(old_prefix):
                        continue
                    new_target = rootfs + target[len(old_prefix):]
                    os.unlink(fpath)
                    os.symlink(new_target, fpath)
                except OSError:
                    pass
    finally:
        signal.signal(signal.SIGINT, old_sigint)
        signal.signal(signal.SIGQUIT, old_sigquit)
