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

# Architecture: Top-level orchestration for `proot-distro build`.
# Steps: load the Dockerfile (file or stdin), parse it, decide whether
# proot is required for this particular Dockerfile (only RUN family
# instructions need it), run the build engine, then write the
# requested output variants (manifest cache always; OCI tarball per
# --output; container install per --install-as).

import os
import re
import shutil
import subprocess
import sys
import tempfile

from proot_distro.constants import (
    CONTAINERS_DIR,
    DOWNLOAD_CACHE_DIR,
    IS_TERMUX,
    PROGRAM_NAME,
    RUNTIME_DIR,
)
from proot_distro.colors import C, msg
from proot_distro.arch import get_device_cpu_arch, normalize_arch
from proot_distro.helpers.dockerfile import (
    DockerfileSyntaxError,
    parse_dockerfile,
)
from proot_distro.helpers.build_engine import (
    BuildEngine,
    BuildError,
    needs_proot,
)
from proot_distro.helpers.oci_writer import (
    build_manifest_and_config,
    store_in_cache,
    write_oci_archive,
)
from proot_distro.helpers.docker import (
    _ARCH_TO_DOCKER,
    _manifest_cache_path,
    parse_image_ref,
)


_NAME_RE = re.compile(r'^[A-Za-z0-9][A-Za-z0-9_.\-]*$')


# ---------------------------------------------------------------------------
# Top-level command
# ---------------------------------------------------------------------------

def command_build(args, configs):  # noqa: ARG001
    """Implements `proot-distro build`."""

    build_path = getattr(args, "path", None) or "."
    dockerfile_path = getattr(args, "dockerfile", None)
    tags = list(getattr(args, "tags", []) or [])
    build_args = _parse_build_args(getattr(args, "build_args", None) or [])
    override_arch = getattr(args, "override_arch", None) or ""
    target_stage = getattr(args, "target_stage", None) or None
    emulator = getattr(args, "emulator", None) or ""
    outputs = list(getattr(args, "outputs", []) or [])
    install_as = getattr(args, "install_as", None) or ""
    no_cache = bool(getattr(args, "no_cache", False))
    force_pull = bool(getattr(args, "force_pull", False))
    verbose = bool(getattr(args, "verbose", False))
    quiet = bool(getattr(args, "quiet", False))

    # ----- resolve build context + Dockerfile -----
    build_dir = os.path.abspath(os.path.expanduser(build_path))
    if dockerfile_path is None:
        dockerfile = os.path.join(build_dir, "Dockerfile")
    elif dockerfile_path == "-":
        dockerfile = "-"
    else:
        dockerfile = os.path.abspath(os.path.expanduser(dockerfile_path))

    if dockerfile != "-" and not os.path.isfile(dockerfile):
        _err(f"Dockerfile not found: '{dockerfile}'.")
        sys.exit(1)

    if not os.path.isdir(build_dir):
        _err(f"Build context '{build_dir}' is not a directory.")
        sys.exit(1)

    # ----- read + parse Dockerfile -----
    try:
        if dockerfile == "-":
            text = sys.stdin.read()
        else:
            with open(dockerfile, "rb") as fh:
                text = fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        _err(f"Cannot read Dockerfile: {exc}")
        sys.exit(1)

    try:
        directives, instructions = parse_dockerfile(text)
    except DockerfileSyntaxError as exc:
        _err(f"Dockerfile syntax error: {exc}")
        sys.exit(1)

    if not instructions:
        _err("Dockerfile contains no instructions.")
        sys.exit(1)

    # ----- proot requirement gate -----
    if needs_proot(instructions):
        if shutil.which("proot") is None:
            _err(
                "This Dockerfile uses RUN, which requires the 'proot' "
                "utility. Install it and try again."
            )
            if IS_TERMUX and sys.stdin.isatty():
                if not _prompt_proot_install():
                    sys.exit(1)
            else:
                if IS_TERMUX:
                    msg(f"{C['CYAN']}Install it with: "
                        f"{C['GREEN']}pkg install proot{C['RST']}")
                msg()
                sys.exit(1)

    # ----- target architecture -----
    if override_arch:
        target_arch = normalize_arch(override_arch)
        if target_arch is None:
            _err(f"Unknown architecture '{override_arch}'.")
            sys.exit(1)
    else:
        target_arch = get_device_cpu_arch()

    # ----- determine the canonical tag set -----
    if not tags:
        derived = _derive_tag_from_path(build_dir, dockerfile)
        if not derived:
            _err(
                "Cannot derive a tag from the build path; pass --tag "
                "explicitly (e.g. --tag myapp:latest)."
            )
            sys.exit(1)
        tags = [derived]

    for t in tags:
        if not _is_valid_tag(t):
            _err(
                f"Tag '{t}' is not valid. A tag must start with an "
                f"alphanumeric character and contain only letters, "
                f"digits, underscores, dots, hyphens, slashes, or a "
                f"single colon for the version."
            )
            sys.exit(1)

    tags = [_with_explicit_tag(t) for t in tags]
    primary_tag = tags[0]

    # ----- run the build -----
    tmp_root = tempfile.mkdtemp(
        prefix="pd-build-", dir=os.path.join(RUNTIME_DIR, "build-tmp")
        if _ensure_dir(os.path.join(RUNTIME_DIR, "build-tmp"))
        else None,
    )

    engine = BuildEngine(
        build_dir=build_dir,
        tmp_root=tmp_root,
        target_arch_pd=target_arch,
        user_build_args=build_args,
        target_stage=target_stage,
        verbose=verbose,
        quiet=quiet,
        no_cache=no_cache,
        force_pull=force_pull,
        emulator=emulator,
    )

    # Single try/except/finally covers the whole post-setup work so
    # KeyboardInterrupt during any phase (engine, cache write, OCI
    # archive write, install-as) is reported cleanly and tmp_root is
    # always removed.
    try:
        try:
            final_stage = engine.run(instructions)
        except BuildError as exc:
            _err(f"Build failed: {exc}")
            sys.exit(1)

        # ----- assemble manifest + image_config -----
        arch_docker = _ARCH_TO_DOCKER.get(target_arch, (target_arch, ""))[0]
        manifest, image_config = build_manifest_and_config(
            final_stage.image_config,
            final_stage.layers,
            arch_docker,
        )

        # Write the manifest cache for every tag so each can be
        # installed offline by name.
        for t in tags:
            try:
                store_in_cache(t, target_arch, manifest, image_config)
            except OSError as exc:
                _err(f"Failed to write manifest cache for '{t}': {exc}")
                sys.exit(1)

        # OCI tarball outputs.
        for out_file in outputs:
            out_abs = os.path.abspath(os.path.expanduser(out_file))
            try:
                if not quiet:
                    msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                        f"Writing OCI archive to "
                        f"'{C['YELLOW']}{out_abs}{C['CYAN']}'...{C['RST']}")
                write_oci_archive(out_abs, manifest, image_config, primary_tag)
            except (OSError, RuntimeError) as exc:
                _err(f"Failed to write '{out_file}': {exc}")
                sys.exit(1)

        # Build summary.
        if not quiet:
            total_size = sum(l["size"] for l in final_stage.layers)
            msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
                f"Build complete.{C['RST']}")
            msg()
            msg(f"{C['CYAN']}Tag(s): "
                f"{C['GREEN']}{', '.join(tags)}{C['RST']}")
            msg(f"{C['CYAN']}Layers: "
                f"{C['GREEN']}{len(final_stage.layers)}"
                f" ({_fmt_size(total_size)} total){C['RST']}")
            msg()

        # Optional --install-as: install the built image as a
        # container directly. The fastest path is to invoke
        # command_install with the primary_tag — pull_image() will
        # find the manifest cache and the layer blobs we just wrote,
        # and install offline.
        if install_as:
            _install_as_container(install_as, primary_tag, target_arch, quiet)

        # Final hint when no --output and no --install-as were given.
        if not outputs and not install_as and not quiet:
            msg(f"{C['CYAN']}Install with: "
                f"{C['GREEN']}{PROGRAM_NAME} install {primary_tag}{C['RST']}")
            msg()
    except KeyboardInterrupt:
        msg(f"{C['BLUE']}[{C['RED']}!{C['BLUE']}] {C['CYAN']}"
            f"Aborted by user.{C['RST']}")
        sys.exit(1)
    finally:
        _cleanup(tmp_root)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _err(text):
    msg()
    msg(f"{C['BRED']}Error: {text}{C['RST']}")
    msg()


def _parse_build_args(raw):
    out = {}
    for item in raw:
        if "=" in item:
            k, _, v = item.partition("=")
        else:
            k, v = item, os.environ.get(item, "")
        if k:
            out[k] = v
    return out


def _derive_tag_from_path(build_dir, dockerfile):
    """Pick a default tag based on the build context basename."""
    base = os.path.basename(os.path.abspath(build_dir).rstrip("/"))
    if not base or base in (".", ".."):
        if dockerfile and dockerfile != "-":
            base = os.path.basename(os.path.dirname(os.path.abspath(dockerfile)))
    base = base.lower()
    base = re.sub(r"[^a-z0-9_.\-]", "-", base).strip("-")
    base = re.sub(r"-+", "-", base)
    if not base or not _NAME_RE.match(base):
        return ""
    return f"{base}:latest"


def _with_explicit_tag(tag):
    """Append ':latest' if `tag`'s last path component lacks a tag part."""
    last = tag.split("/")[-1]
    return tag if ":" in last else tag + ":latest"


def _is_valid_tag(tag):
    # Allow registry-style prefixes (host/path/name:tag) without
    # mandating registry validation; insist only that the local alias
    # we'd derive is sane.
    if not tag:
        return False
    if ":" in tag:
        name_part, tag_part = tag.rsplit(":", 1)
        if not tag_part:
            return False
        # Tag part: starts with alphanumeric, then word chars + dot/dash.
        if not re.match(r"^[A-Za-z0-9][\w.\-]*$", tag_part):
            return False
    else:
        name_part = tag
    last = name_part.split("/")[-1]
    return bool(_NAME_RE.match(last))


def _ensure_dir(path):
    try:
        os.makedirs(path, exist_ok=True)
        return True
    except OSError:
        return False


def _cleanup(tmp_root):
    try:
        shutil.rmtree(tmp_root, ignore_errors=True)
    except OSError:
        pass


def _fmt_size(n):
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    f = float(n)
    for u in units:
        if f < 1024.0:
            return f"{f:.1f} {u}"
        f /= 1024.0
    return f"{f:.1f} PiB"


def _prompt_proot_install():
    sys.stderr.write(
        f"{C['CYAN']}Would you like to install it now? [y/N] {C['RST']}"
    )
    sys.stderr.flush()
    try:
        answer = input().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    if answer not in ("y", "yes"):
        msg(f"{C['CYAN']}Install it manually with: "
            f"{C['GREEN']}pkg install proot{C['RST']}")
        return False
    try:
        subprocess.run(["pkg", "install", "-y", "-q", "proot"], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
        _err(f"Failed to install proot: {exc}")
        return False
    return True


def _install_as_container(install_name, image_ref, target_arch, quiet):
    """Run the install command for `image_ref` aliased as `install_name`."""
    from proot_distro.commands.install import command_install

    # Validate the install_name early so we don't half-create a
    # container directory and then bail.
    if not _NAME_RE.match(install_name):
        _err(
            f"--install-as name '{install_name}' is not valid. "
            f"It must start with a letter or digit and contain only "
            f"letters, digits, underscores, dots, or hyphens."
        )
        sys.exit(1)

    container_dir = os.path.join(CONTAINERS_DIR, install_name)
    rootfs_dir = os.path.join(container_dir, "rootfs")
    if os.path.isdir(rootfs_dir):
        _err(
            f"Container '{install_name}' already exists. Use "
            f"'{PROGRAM_NAME} remove {install_name}' first, or "
            f"'{PROGRAM_NAME} reset {install_name}' to rebuild."
        )
        sys.exit(1)

    if not quiet:
        msg(f"{C['BLUE']}[{C['GREEN']}*{C['BLUE']}] {C['CYAN']}"
            f"Installing built image as "
            f"'{C['YELLOW']}{install_name}{C['CYAN']}'..."
            f"{C['RST']}")

    class _Args:
        pass

    a = _Args()
    a.alias = image_ref
    a.custom_dist_name = install_name
    a.override_arch = target_arch
    command_install(a, {})
