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

# Architecture: Assembly of the proot(1) argv that command_login
# ultimately exec's. Sub-pieces are emitted in a fixed order:
#
#   1. proot binary + emulator flags (when cross-arch).
#   2. Proot extensions on Termux (--kill-on-exit, --link2symlink,
#      --sysvipc, --kernel-release, -L).
#   3. --change-id for non-termux containers.
#   4. --rootfs / --cwd / baseline /dev /proc /sys binds.
#   5. Optional non-minimal binds: /dev/random etc., Android storage,
#      Android system paths, Termux $HOME / $PREFIX bridges.
#   6. User-supplied --bind entries (with overlap warning).
#   7. Inner command (shell or run-mode inner argv).

import os
import sys

from proot_distro.constants import (
    DEFAULT_FAKE_KERNEL_VERSION,
    IS_TERMUX,
    TERMUX_PREFIX,
    TERMUX_APP_PACKAGE,
    TERMUX_HOME,
)
from proot_distro import dirfd
from proot_distro.message import crit_error, warn
from proot_distro.arch import ARCH_UNAME_M
from proot_distro.shm import make_guest_tmp, make_shm_dir
from proot_distro.sysdata import fake_sysdata_bindings
from proot_distro.commands.login.bindings import (
    storage_bindings, system_bindings,
)


def build_proot_args(
    *,
    proot_bin,
    rootfs, login_wd, rootfs_arg=None,
    container_fd=None, rootfs_fd=None,
    login_uid, login_gid, login_home,
    emu_args, need_emu,
    target_arch, hostname, kernel_release,
    dist_type, minimal, isolated,
    no_link2symlink, no_sysvipc, no_kill_on_exit,
    use_shared_home, shared_tmp, shared_x11,
    custom_binds, redirect_ports,
    inner,
):
    """Assemble the full proot command-line argv. Exits on bad --bind input.

    *rootfs* is the container's rootfs path, used to compose the bind
    sources that live inside it. *rootfs_arg* is what `--rootfs=` itself
    is given, which is not the same thing: the caller normally passes
    "." and chdirs into the descriptor it pinned just before the exec, so
    proot resolves the guest root against getcwd() -- the inode -- rather
    than against a name a concurrent session can re-point. It defaults to
    *rootfs*, and `--get-proot-cmd` passes the path explicitly, since
    that command line is printed for the user to run from their own
    working directory. A printed command cannot carry the pin -- a
    descriptor is not something a shell line can hold -- so the caller
    says as much next to it rather than letting the path form pass for
    what the program itself runs.

    *container_fd* and *rootfs_fd* are the two directories the caller
    pinned. The directories made here on the host side -- the container's
    shm store, its sysdata stubs, the guest's /tmp, a termux-type guest's
    cache dir -- are created and chmod'ed through them rather than from
    a name walked afresh. What comes back is still a path, because a bind
    source is a name proot resolves for itself; the pin covers our half
    of it, not proot's.
    """
    args = [proot_bin] + list(emu_args)

    _add_proot_extensions(
        args,
        target_arch=target_arch, hostname=hostname,
        kernel_release=kernel_release,
        dist_type=dist_type, minimal=minimal,
        no_link2symlink=no_link2symlink,
        no_sysvipc=no_sysvipc,
        no_kill_on_exit=no_kill_on_exit,
    )

    if dist_type != "termux":
        args.append(f"--change-id={login_uid}:{login_gid}")

    args.append(f"--rootfs={rootfs_arg or rootfs}")
    args.append(f"--cwd={login_wd}")
    args += ["--bind=/dev", "--bind=/proc", "--bind=/sys"]

    if not minimal:
        _add_non_minimal_binds(
            args,
            rootfs=rootfs, login_home=login_home, login_uid=login_uid,
            dist_type=dist_type, isolated=isolated, need_emu=need_emu,
            use_shared_home=use_shared_home,
            shared_tmp=shared_tmp, shared_x11=shared_x11,
            container_fd=container_fd, rootfs_fd=rootfs_fd,
        )

    _add_custom_binds(args, custom_binds)

    if redirect_ports:
        args.append("-p")

    args += inner
    return args


def _add_proot_extensions(
    args,
    *,
    target_arch, hostname, kernel_release,
    dist_type, minimal,
    no_link2symlink, no_sysvipc, no_kill_on_exit,
):
    if not IS_TERMUX:
        return
    if no_kill_on_exit:
        warn("option '--no-kill-on-exit' is enabled, after logout your "
             "session will be blocked until all processes are terminated.")
    else:
        args.append("--kill-on-exit")

    if dist_type != "termux" and not no_link2symlink:
        args.append("--link2symlink")

    if not no_sysvipc and not minimal:
        args.append("--sysvipc")

    if not minimal:
        uname_m = ARCH_UNAME_M.get(target_arch, os.uname().machine)
        args.append(
            f"--kernel-release=\\Linux\\{hostname}\\{kernel_release}"
            f"\\{DEFAULT_FAKE_KERNEL_VERSION}\\{uname_m}\\localdomain\\-1\\"
        )

    args.append("-L")  # Fix lstat for dpkg symlink warnings.


def _add_non_minimal_binds(
    args,
    *,
    rootfs, login_home, login_uid,
    dist_type, isolated, need_emu,
    use_shared_home, shared_tmp, shared_x11,
    container_fd=None, rootfs_fd=None,
):
    if dist_type != "termux" and IS_TERMUX:
        _add_termux_dev_binds(args, rootfs, container_fd, rootfs_fd)

    if IS_TERMUX and not isolated:
        # Dalvik/ART caches and shared storage are host-domain Android
        # paths bound for both distro types in the default mode.
        _add_dalvik_cache_binds(args)
        args += storage_bindings()
        # The Termux app's private dirs (apps, cache, $HOME) are bound
        # only for normal-type containers. A termux-type guest ships its
        # own /data/data/com.termux and must never see the host's.
        if dist_type != "termux":
            _add_termux_app_binds(args)

    # Android system directories (/apex, /system, /vendor, …). Bound for
    # normal-type when not isolated, or when emulating (the QEMU loader
    # needs them), and for termux-type only when not isolated. Fully
    # isolated sessions of either type get no host directories.
    if IS_TERMUX and (not isolated or need_emu):
        args += system_bindings()
        if dist_type != "termux":
            args.append(f"--bind={TERMUX_PREFIX}")

    # A termux-type guest still needs its own cache dir to exist; create
    # it inside the rootfs (never bound from the host). Level by level off
    # a descriptor, since every component of that path is image content:
    # os.makedirs() would have built the tree wherever a `data` symlink
    # pointed, which on the host side of proot is anywhere the user can
    # write. Nothing depends on the result, so a refusal is silent.
    if IS_TERMUX and dist_type == "termux" and not isolated:
        parts = ("data", "data", TERMUX_APP_PACKAGE, "cache")
        if rootfs_fd is not None:
            dirfd.makedirs_at(rootfs_fd, rootfs, parts)
        else:
            dirfd.makedirs_under(rootfs, parts)

    if use_shared_home:
        if dist_type == "termux":
            args.append(f"--bind={TERMUX_HOME}:{TERMUX_HOME}")
        elif login_uid == "0":
            args.append(f"--bind={TERMUX_HOME}:/root")
        else:
            args.append(f"--bind={TERMUX_HOME}:{login_home}")

    if IS_TERMUX and shared_tmp and dist_type != "termux":
        args.append(f"--bind={TERMUX_PREFIX}/tmp:/tmp")
    if IS_TERMUX and shared_x11 and dist_type != "termux":
        args.append(f"--bind={TERMUX_PREFIX}/tmp/.X11-unix:/tmp/.X11-unix")


def _add_termux_dev_binds(args, rootfs, container_fd=None, rootfs_fd=None):
    """Bind device files and fake /proc/sys substitutes used by Termux."""
    args.append("--bind=/dev/urandom:/dev/random")
    if not os.path.lexists("/dev/fd"):
        args.append("--bind=/proc/self/fd:/dev/fd")
    for i, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
        if not os.path.lexists(f"/dev/{name}") and os.path.exists(f"/proc/self/fd/{i}"):
            args.append(f"--bind=/proc/self/fd/{i}:/dev/{name}")
    args += fake_sysdata_bindings(rootfs, container_fd=container_fd)

    # /dev/shm comes from the container's own directory, not from a name
    # inside the rootfs: proot resolves a bind source when it mounts it,
    # long after this check, and every session of the container can write
    # to the rootfs root. Binding `<rootfs>/tmp` therefore let a guest
    # flip that name to a symlink in the window before the exec and have
    # the next session mount a host directory of its choosing — under
    # --isolated too, which is the one mode meant to bind nothing of the
    # host at all. See proot_distro.shm.
    make_guest_tmp(rootfs, rootfs_fd=rootfs_fd)
    shm = make_shm_dir(rootfs, container_fd=container_fd)
    if shm is not None:
        args.append(f"--bind={shm}:/dev/shm")
    else:
        warn("container's shm directory is not a plain directory; starting "
             "without the /dev/shm bind.")


def _add_dalvik_cache_binds(args):
    """Bind the host's Dalvik/ART caches (both distro types).

    These are Android-system caches, not the Termux app's private data,
    so both normal-type and termux-type guests get them. Each dir must
    carry the world-execute bit to be traversable from the guest user.
    """
    for data_dir in (
        "/data/app", "/data/dalvik-cache",
        "/data/misc/apexdata/com.android.art/dalvik-cache",
    ):
        if not os.path.isdir(data_dir):
            continue
        mode = oct(os.stat(data_dir).st_mode)[-1]
        if mode in ("1", "5", "7"):
            args.append(f"--bind={data_dir}")


def _add_termux_app_binds(args):
    """Bind the Termux app's private dirs (apps, cache, $HOME).

    Normal-type containers only: a termux-type guest must not see the
    host's /data/data/com.termux, so this is never called for it.
    """
    apps_dir = f"/data/data/{TERMUX_APP_PACKAGE}/files/apps"
    if os.path.isdir(apps_dir):
        args.append(f"--bind={apps_dir}")

    args.append(f"--bind=/data/data/{TERMUX_APP_PACKAGE}/cache")
    args.append(f"--bind={TERMUX_HOME}")


def _add_custom_binds(args, custom_binds):
    """Append user-supplied --bind entries with overlap detection."""
    existing_dsts = set()
    for arg in args:
        if not arg.startswith("--bind="):
            continue
        spec = arg[len("--bind="):]
        colon = spec.find(":")
        dst_part = spec[colon + 1:] if colon != -1 else spec
        existing_dsts.add(os.path.normpath(dst_part))

    for bnd in custom_binds:
        if not bnd:
            crit_error("bind specification cannot be empty.")
            sys.exit(1)
        if ":" in bnd:
            src, dst = bnd.split(":", 1)
        else:
            src, dst = bnd, None
        if not src:
            crit_error(f"bind source path cannot be empty in '--bind={bnd}'.")
            sys.exit(1)
        src = os.path.abspath(src)
        if dst is not None and not os.path.isabs(dst):
            crit_error(
                f"binding destination must be an absolute path, got '{dst}'."
            )
            sys.exit(1)
        effective_dst = os.path.normpath(dst if dst is not None else src)
        if effective_dst in existing_dsts:
            warn(f"binding '--bind={bnd}' overlaps with an existing one "
                 f"at destination '{effective_dst}'.")
        existing_dsts.add(effective_dst)
        args.append(f"--bind={src}:{dst}" if dst else f"--bind={src}")
