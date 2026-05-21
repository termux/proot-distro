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

# Architecture: Guest environment assembly and persistence.
#
#   - read_manifest_env: harvest Env entries from containers/<name>/manifest.json
#   - IMAGE_ENV_BLOCKED: vars an image Env may NOT override.
#   - inject_termux_profile: drop a profile.d snippet that re-exports
#     proot-distro-set vars when a login shell re-sources /etc/profile.

import json
import os
import re

from proot_distro.constants import TERMUX_PREFIX


# Conservative identifier syntax for env var names: a leading letter or
# underscore followed by letters, digits, or underscores. Image Env
# entries and user-supplied --env flags are filtered against this before
# they reach the profile.d snippet — otherwise a name carrying spaces,
# quotes, or `;` would break the sourced script.
_VALID_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


# Vars the image Env must not override. Some are proot-distro-defined
# values; others are host-inherited terminal vars that must remain
# under the launcher's control regardless of image configuration.
IMAGE_ENV_BLOCKED = frozenset({
    "ANDROID_ART_ROOT", "ANDROID_DATA", "ANDROID_I18N_ROOT",
    "ANDROID_ROOT", "ANDROID_RUNTIME_ROOT", "ANDROID_TZDATA_ROOT",
    "BOOTCLASSPATH", "DEX2OATBOOTCLASSPATH", "EXTERNAL_STORAGE",
    "MOZ_FAKE_NO_SANDBOX", "PULSE_SERVER",
    "TERM", "COLORTERM",
})


# Per-session vars (HOME, USER, TERM, COLORTERM) belong to the spawning
# shell, not the container — baking them in would override the values
# set by `su - <other-user>`. PATH is handled specially with a
# case-guarded append. Proot-internal vars are host-side and have no
# meaning inside the guest.
_PROFILE_INJECT_SKIP = frozenset({
    "HOME", "USER", "TERM", "COLORTERM",
    "PATH",
    "PROOT_NO_SECCOMP", "PROOT_VERBOSE", "PROOT_L2S_DIR",
    "LD_PRELOAD", "LD_LIBRARY_PATH",
})


def read_manifest_env(container_dir: str) -> list:
    """Return image Env entries from manifest.json, or [] if absent/invalid."""
    manifest_path = os.path.join(container_dir, "manifest.json")
    try:
        with open(manifest_path) as fh:
            data = json.load(fh)
        env = (data.get("image_config") or {}).get("config", {}).get("Env") or []
        return [e for e in env if isinstance(e, str) and "=" in e]
    except (OSError, ValueError):
        return []


def inject_termux_profile(rootfs: str, env: dict) -> None:
    """Write a profile.d snippet that re-applies the login-time environment.

    Login shells source /etc/profile, which reinitialises the environment
    and discards whatever proot inherited. Without a snippet every
    proot-distro-defined var — Termux baseline (MOZ_FAKE_NO_SANDBOX,
    PULSE_SERVER), Android system vars, image Env entries, and user
    --env flags — disappears the moment the user runs `su - someone`
    inside the container.

    PATH gets a case-guarded append so the system PATH from /etc/profile
    keeps priority. Other vars are exported unconditionally so the
    proot-distro value wins. Per-session vars and proot-internal vars
    are excluded via _PROFILE_INJECT_SKIP.
    """
    profile_d = os.path.join(rootfs, "etc", "profile.d")
    if not os.path.isdir(profile_d):
        return
    snippet = os.path.join(profile_d, "termux-profile.sh")
    # Remove the legacy filename (PATH-only era) so a previously-used
    # container doesn't keep sourcing stale content.
    legacy_snippet = os.path.join(profile_d, "termux-prefix.sh")
    try:
        os.remove(legacy_snippet)
    except OSError:
        pass
    termux_bin = f"{TERMUX_PREFIX}/bin"

    lines = [
        'case ":${PATH}:" in',
        f'  *":{termux_bin}:"*) ;;',
        f'  *) export PATH="${{PATH}}:{termux_bin}" ;;',
        'esac',
    ]

    for key in sorted(env):
        if key in _PROFILE_INJECT_SKIP:
            continue
        if not _VALID_ENV_KEY_RE.match(key):
            # A malformed name (spaces, ';', quotes …) would corrupt the
            # snippet when /etc/profile sources it. Drop the entry rather
            # than write a line that breaks every subsequent shell.
            continue
        val = env[key]
        # Single-quote the value; embedded single quotes use the
        # standard '\'' idiom (close-quote, escaped quote, reopen-quote).
        escaped = str(val).replace("'", "'\\''")
        lines.append(f"export {key}='{escaped}'")

    content = "\n".join(lines) + "\n"
    try:
        with open(snippet, "w") as fh:
            fh.write(content)
        os.chmod(snippet, 0o644)
    except OSError:
        pass
