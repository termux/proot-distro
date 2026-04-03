"""
Proot-Distro - manage proot containers on Termux.

Created by Sylirre <sylirre@termux.dev> for Termux project.
Development assisted by Claude Code (https://claude.ai/code).

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""
import os
import re
import shutil
import sys
from dataclasses import dataclass, field

try:
    import yaml
except ImportError:
    print("Error: PyYAML is required. Install with: pip install pyyaml", file=sys.stderr)
    sys.exit(1)

from proot_distro.constants import PD_CONFIGS_DIR

# Configs bundled with the package (lowest priority; overridden by PD_CONFIGS_DIR).
_BUNDLED_CONFIGS_DIR = os.path.join(os.path.dirname(__file__), "configs")
from proot_distro.colors import C, msg

_PLACEHOLDER_RE = re.compile(r'\$\{([^}]+)\}')


def _expand_var(expr: str, version: str, architecture: str) -> str:
    if expr == "architecture":
        return architecture
    if expr == "version":
        return version
    if expr.startswith("version:"):
        parts = expr.split(":")
        try:
            offset = int(parts[1])
            if len(parts) >= 3:
                return version[offset:offset + int(parts[2])]
            return version[offset:]
        except (IndexError, ValueError) as exc:
            msg(f"{C['BRED']}Warning: invalid version slice '${{{expr}}}': {exc}{C['RST']}")
            return f"${{{expr}}}"
    msg(f"{C['BRED']}Warning: unknown placeholder '${{{expr}}}' — left unexpanded{C['RST']}")
    return f"${{{expr}}}"


def expand_url(url: str, version: str, architecture: str) -> str:
    """Expand ${version}, ${version:offset:length}, and ${architecture} in a URL."""
    return _PLACEHOLDER_RE.sub(
        lambda m: _expand_var(m.group(1), version, architecture),
        url,
    )


@dataclass
class ArchEntry:
    arch: str
    url: str
    checksum: str = ""


@dataclass
class DistroConfig:
    alias: str
    config_path: str
    name: str
    version: str
    architectures: list  # list[ArchEntry]
    description: str = ""
    post_install_automation: str = ""
    strip_path_components: int = 0
    dist_type: str = "normal"


def _parse_arch_entries(raw: list) -> list:
    entries = []
    for item in raw:
        if not isinstance(item, dict):
            raise ValueError(f"Expected a mapping in architectures list, got {type(item).__name__}")
        checksum = str(item.get("checksum", ""))
        for key, value in item.items():
            if key == "checksum":
                continue
            entries.append(ArchEntry(arch=str(key), url=str(value), checksum=checksum))
            break
    return entries


def load_config(path: str, alias: str) -> DistroConfig:
    with open(path) as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ValueError("Config file does not contain a YAML mapping")
    dist_type = str(data.get("type", "normal"))
    if dist_type not in ("normal", "termux"):
        raise ValueError(f"Unsupported type '{dist_type}'. Valid values: normal, termux.")
    return DistroConfig(
        alias=alias,
        config_path=path,
        name=str(data.get("name", alias)),
        version=str(data.get("version", "")),
        architectures=_parse_arch_entries(data.get("architectures", [])),
        description=str(data.get("description", "")),
        post_install_automation=str(data.get("post_install_automation", "") or ""),
        strip_path_components=int(data.get("strip_path_components", 0)),
        dist_type=dist_type,
    )


def discover_configs() -> dict:
    """Return {alias: DistroConfig} for all configs present in PD_CONFIGS_DIR.

    Bundled configs are not read directly; they must be populated into
    PD_CONFIGS_DIR first via _ensure_config() or _ensure_all_configs().
    """
    configs: dict = {}
    if not os.path.isdir(PD_CONFIGS_DIR):
        return configs

    for fname in sorted(os.listdir(PD_CONFIGS_DIR)):
        if not fname.endswith(".yaml"):
            continue
        full = os.path.join(PD_CONFIGS_DIR, fname)
        if not os.path.isfile(full):
            continue
        alias = fname[: -len(".yaml")]
        try:
            configs[alias] = load_config(full, alias)
        except Exception as exc:
            msg(f"{C['BRED']}Warning: failed to load config for '{alias}': {exc}{C['RST']}")
    return configs


def _ensure_config(alias: str) -> None:
    """Copy the bundled config for *alias* into PD_CONFIGS_DIR if absent."""
    dest = os.path.join(PD_CONFIGS_DIR, alias + ".yaml")
    if os.path.isfile(dest):
        return
    src = os.path.join(_BUNDLED_CONFIGS_DIR, alias + ".yaml")
    if not os.path.isfile(src):
        return
    os.makedirs(PD_CONFIGS_DIR, exist_ok=True)
    shutil.copy2(src, dest)


def _ensure_all_configs() -> None:
    """Copy every bundled config into PD_CONFIGS_DIR if not already there."""
    if not os.path.isdir(_BUNDLED_CONFIGS_DIR):
        return
    os.makedirs(PD_CONFIGS_DIR, exist_ok=True)
    for fname in os.listdir(_BUNDLED_CONFIGS_DIR):
        if not fname.endswith(".yaml"):
            continue
        dest = os.path.join(PD_CONFIGS_DIR, fname)
        if not os.path.isfile(dest):
            shutil.copy2(os.path.join(_BUNDLED_CONFIGS_DIR, fname), dest)


def is_bundled_config(alias: str) -> bool:
    """Return True if *alias* has a config shipped with the package."""
    return os.path.isfile(os.path.join(_BUNDLED_CONFIGS_DIR, alias + ".yaml"))


def config_file_for_alias(alias: str) -> str:
    """Return the config file path for *alias*, or empty string."""
    p = os.path.join(PD_CONFIGS_DIR, alias + ".yaml")
    return p if os.path.isfile(p) else ""


def _write_yaml(path: str, data: dict) -> None:
    """Write *data* as YAML to *path* using manual formatting for clean output."""
    lines = []
    for key, value in data.items():
        if isinstance(value, str) and "\n" in value:
            lines.append(f"{key}: |")
            for line in value.splitlines():
                lines.append(f"  {line}")
        elif isinstance(value, list):
            lines.append(f"{key}:")
            for item in value:
                if isinstance(item, dict):
                    first = True
                    for k, v in item.items():
                        prefix = "  - " if first else "    "
                        lines.append(f'{prefix}{k}: "{v}"')
                        first = False
                else:
                    lines.append(f"  - {item}")
        else:
            lines.append(f'{key}: "{value}"')
    lines.append("")
    with open(path, "w") as fh:
        fh.write("\n".join(lines))
