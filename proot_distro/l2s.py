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


# Prefix used by proot's --link2symlink extension when naming the
# intermediate file that stands in for a hard-link. See
# proot/src/extension/link2symlink/link2symlink.c: ".proot.l2s." in
# userland builds (the Termux default) and ".l2s." otherwise. Both
# spellings are accepted so layers produced by either build are
# recognised.
_L2S_NAME_PREFIXES = (".proot.l2s.", ".l2s.")


def resolve_l2s_target(symlink_full: str, target: str, rootfs: str):
    """Return abs path of an l2s intermediate file if `target` looks like one.

    `symlink_full` is the absolute path of the symlink whose readlink
    returned `target`; `rootfs` is the container rootfs root, used only
    to confine the resolved path to the rootfs subtree. Returns None
    when `target` is not an l2s intermediate or when the resolved path
    would escape `rootfs`.

    proot's --link2symlink extension emulates hard links by replacing
    the original path with a symlink to an intermediate file whose
    basename is ``<PREFIX><name><4-digit-count>``. The intermediate is
    itself a symlink to a final ``.NNNN``-suffixed file holding the
    actual content. The intermediate's parent directory depends on
    proot's PROOT_L2S_DIR: when set, every intermediate lives in that
    one directory (proot-distro sets it to ``<rootfs>/.l2s``); when
    unset, the intermediate is created next to the original. Detection
    is therefore by basename prefix, not by directory, so symlinks are
    recognised regardless of where the producing proot stashed them.

    Callers (backup, build layer writer) materialise the symlink as
    the backing file's content via os.stat / open() on the returned
    path — both follow symlinks and land on the final file automatically.
    """
    name = os.path.basename(target)
    if not name.startswith(_L2S_NAME_PREFIXES):
        return None
    if os.path.isabs(target):
        abs_target = os.path.normpath(target)
    else:
        abs_target = os.path.normpath(
            os.path.join(os.path.dirname(symlink_full), target)
        )
    rootfs_abs = os.path.abspath(rootfs)
    if abs_target != rootfs_abs and not abs_target.startswith(
        rootfs_abs + os.sep
    ):
        return None
    return abs_target


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
