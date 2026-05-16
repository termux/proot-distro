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

# Architecture: All global constants and path variables for proot-distro.
# On Termux/Android, paths are rooted under TERMUX_PREFIX (env var). On a
# regular Linux host, XDG base directories (~/.local/share, ~/.cache) are
# used instead. IS_TERMUX is computed once at import time and drives both
# path selection and runtime behaviour (e.g. isolated-mode default).

import os
import platform
from importlib.metadata import version, PackageNotFoundError

PROGRAM_NAME = "proot-distro"

try:
    PROGRAM_VERSION = version("proot-distro")
except PackageNotFoundError:
    PROGRAM_VERSION = "rolling"

os.umask(0o022)

# Keep LD_PRELOAD for restoring after proot invocations.
_SAVED_LD_PRELOAD = os.environ.get("LD_PRELOAD", "")


# ---------------------------------------------------------------------------
# Termux / Android detection
# ---------------------------------------------------------------------------

def _detect_termux() -> bool:
    """Return True when running inside Termux on Android."""
    # Termux-specific env var — always set by the Termux shell.
    if os.environ.get("TERMUX_PREFIX"):
        return True
    # Standard Android system env var present in every Android process.
    if os.environ.get("ANDROID_ROOT"):
        return True
    # platform.platform() reports "android" on Python builds for Android.
    try:
        if "android" in platform.platform().lower():
            return True
    except Exception:
        pass
    # /system/build.prop exists on every Android device.
    if os.path.isfile("/system/build.prop"):
        return True
    return False


IS_TERMUX: bool = _detect_termux()


# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------

PREFIX = os.environ.get(
    "TERMUX_PREFIX", "/data/data/com.termux/files/usr"
)
TERMUX_HOME = os.environ.get("HOME", "/data/data/com.termux/files/home")
TERMUX_APP_PACKAGE = os.environ.get("TERMUX_APP_PACKAGE", "com.termux")

if IS_TERMUX:
    RUNTIME_DIR = os.path.join(PREFIX, "var", "lib", "proot-distro")
    DOWNLOAD_CACHE_DIR = os.path.join(RUNTIME_DIR, "dlcache")
else:
    _xdg_data = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    _xdg_cache = os.environ.get("XDG_CACHE_HOME") or os.path.join(
        os.path.expanduser("~"), ".cache"
    )
    RUNTIME_DIR = os.path.join(_xdg_data, "proot-distro")
    DOWNLOAD_CACHE_DIR = os.path.join(_xdg_cache, "proot-distro")

# New container storage layout: containers/<name>/manifest.json + rootfs/
CONTAINERS_DIR = os.path.join(RUNTIME_DIR, "containers")

# Legacy rootfs path — used only for migrating old installations.
LEGACY_ROOTFS_DIR = os.path.join(RUNTIME_DIR, "installed-rootfs")

# Layer and manifest caches (subdirectories of the download cache).
LAYER_CACHE_DIR = os.path.join(DOWNLOAD_CACHE_DIR, "layers")
MANIFEST_CACHE_DIR = os.path.join(DOWNLOAD_CACHE_DIR, "manifests")

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_PRIMARY_NS = "8.8.8.8"
DEFAULT_SECONDARY_NS = "8.8.4.4"

if IS_TERMUX:
    DEFAULT_PATH_ENV = (
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ":/usr/local/games:/usr/games"
        f":{PREFIX}/bin:/system/bin:/system/xbin"
    )
else:
    DEFAULT_PATH_ENV = (
        "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
        ":/usr/local/games:/usr/games"
    )

DEFAULT_FAKE_KERNEL_RELEASE = "6.17.0-PRoot-Distro"
DEFAULT_FAKE_KERNEL_VERSION = (
    "#1 SMP PREEMPT_DYNAMIC Fri, 10 Oct 2025 00:00:00 +0000"
)
