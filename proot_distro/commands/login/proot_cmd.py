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
from proot_distro.message import crit_error, warn
from proot_distro.arch import ARCH_UNAME_M
from proot_distro.sysdata import fake_proc_bindings
from proot_distro.commands.login.bindings import (
    storage_bindings, system_bindings,
)


def build_proot_args(
    *,
    proot_bin,
    rootfs, login_wd,
    login_uid, login_gid, login_home,
    emu_args, need_emu,
    target_arch, hostname, kernel_release,
    dist_type, minimal, isolated,
    no_link2symlink, no_sysvipc, no_kill_on_exit,
    use_shared_home, shared_tmp, shared_x11,
    custom_binds, redirect_ports,
    inner,
):
    """Assemble the full proot command-line argv. Exits on bad --bind input."""
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

    args.append(f"--rootfs={rootfs}")
    args.append(f"--cwd={login_wd}")
    args += ["--bind=/dev", "--bind=/proc", "--bind=/sys"]

    if not minimal:
        _add_non_minimal_binds(
            args,
            rootfs=rootfs, login_home=login_home, login_uid=login_uid,
            dist_type=dist_type, isolated=isolated, need_emu=need_emu,
            use_shared_home=use_shared_home,
            shared_tmp=shared_tmp, shared_x11=shared_x11,
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
):
    if dist_type != "termux" and IS_TERMUX:
        _add_termux_dev_binds(args, rootfs)

    if IS_TERMUX and not isolated:
        _add_android_data_binds(args, rootfs, dist_type)
        args += storage_bindings()

    if IS_TERMUX and (dist_type == "termux" or not isolated or need_emu):
        args += system_bindings()
        if dist_type != "termux":
            args.append(f"--bind={TERMUX_PREFIX}")

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


def _add_termux_dev_binds(args, rootfs):
    """Bind device files and fake /proc/sys substitutes used by Termux."""
    args += [
        "--bind=/dev/urandom:/dev/random",
        "--bind=/proc/self/fd:/dev/fd",
    ]
    for i, name in ((0, "stdin"), (1, "stdout"), (2, "stderr")):
        if os.path.exists(f"/proc/self/fd/{i}"):
            args.append(f"--bind=/proc/self/fd/{i}:/dev/{name}")
    sysdata_dir = os.path.join(os.path.dirname(rootfs), "sysdata")
    args.append(f"--bind={sysdata_dir}/sys_empty:/sys/fs/selinux")
    args += fake_proc_bindings(rootfs)

    tmp_dir = os.path.join(rootfs, "tmp")
    os.makedirs(tmp_dir, exist_ok=True)
    try:
        os.chmod(tmp_dir, 0o1777)
    except OSError:
        pass
    args.append(f"--bind={tmp_dir}:/dev/shm")


def _add_android_data_binds(args, rootfs, dist_type):
    """Bind Android dalvik caches + Termux app cache + Termux home."""
    for data_dir in (
        "/data/app", "/data/dalvik-cache",
        "/data/misc/apexdata/com.android.art/dalvik-cache",
    ):
        if not os.path.isdir(data_dir):
            continue
        mode = oct(os.stat(data_dir).st_mode)[-1]
        if mode in ("1", "5", "7"):
            args.append(f"--bind={data_dir}")

    apps_dir = f"/data/data/{TERMUX_APP_PACKAGE}/files/apps"
    if os.path.isdir(apps_dir):
        args.append(f"--bind={apps_dir}")

    if dist_type != "termux":
        args.append(f"--bind=/data/data/{TERMUX_APP_PACKAGE}/cache")
        args.append(f"--bind={TERMUX_HOME}")
    else:
        os.makedirs(
            os.path.join(rootfs, "data", "data", TERMUX_APP_PACKAGE, "cache"),
            exist_ok=True,
        )


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
