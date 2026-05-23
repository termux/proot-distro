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

# Architecture: Builds the lists of --bind arguments for two Android
# domains:
#
#   storage_bindings — /sdcard / /storage entries; only useful when
#                      Termux app has been granted storage permission.
#   system_bindings  — /apex, /system, /vendor, linker config files,
#                      etc. Each path is filtered through realpath +
#                      stat: directories must carry the world-execute
#                      bit (modes 1/5/7) so they are traversable from
#                      the guest user, and files must open for read.

import os


def storage_bindings() -> list:
    """Return --bind args for Android shared storage."""
    binds = []
    if os.access("/storage", os.R_OK):
        binds += ["--bind=/storage"]
        if os.access("/storage/emulated/0", os.R_OK):
            binds += [
                "--bind=/storage/emulated/0:/sdcard",
                "--bind=/storage/emulated/0:/mnt/sdcard",
            ]
    else:
        for p in ("/storage/self/primary", "/storage/emulated/0", "/sdcard"):
            if os.access(p, os.R_OK):
                binds += [
                    f"--bind={p}:/mnt/sdcard",
                    f"--bind={p}:/sdcard",
                    f"--bind={p}:/storage/emulated/0",
                    f"--bind={p}:/storage/self/primary",
                ]
                break
    return binds


def system_bindings() -> list:
    """Return --bind args for Android system paths reachable by the guest."""
    binds = []
    for path in (
        "/apex", "/odm", "/product", "/system", "/system_ext", "/vendor",
        "/linkerconfig/ld.config.txt",
        "/linkerconfig/com.android.art/ld.config.txt",
        "/plat_property_contexts", "/property_contexts",
    ):
        try:
            real = os.path.realpath(path)
        except OSError:
            continue
        if not os.path.exists(real):
            continue
        if os.path.isdir(real):
            mode = oct(os.stat(real).st_mode)[-1]
            if mode in ("1", "5", "7"):
                binds.append(f"--bind={real}")
        elif os.path.isfile(real):
            try:
                with open(real, "rb") as fh:
                    fh.read(1)
                binds.append(f"--bind={real}")
            except OSError:
                pass
    return binds
