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
import sys
import tempfile
from contextlib import ExitStack
from types import SimpleNamespace

from proot_distro import dirfd, statedir
from proot_distro.commands.install import command_install

from proot_distro.constants import (
    PROGRAM_NAME,
    RUNTIME_DIR,
)
from proot_distro.paths import container_is_installed
from proot_distro.message import (
    C, msg, log_info, log_error, crit_error, quote_error,
)
from proot_distro.locking import BuildLock
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
from proot_distro.helpers.docker import ARCH_TO_DOCKER, with_explicit_tag
from proot_distro.names import is_valid_name, require_valid_name
from proot_distro.progress import fmt_size


# ---------------------------------------------------------------------------
# Top-level command
# ---------------------------------------------------------------------------

def command_build(args):
    """Implements `proot-distro build`."""

    build_path = getattr(args, "path", None) or "."
    dockerfile_path = getattr(args, "dockerfile", None)
    tags = list(getattr(args, "tags", []) or [])
    build_args = _parse_build_args(getattr(args, "build_args", None) or [])
    override_arch = getattr(args, "override_arch", None) or ""
    target_stage = getattr(args, "target_stage", None) or None
    emulator = getattr(args, "emulator", None) or ""
    outputs = list(getattr(args, "outputs", []) or [])
    install_as = getattr(args, "install_as", None)

    if dockerfile_path is not None and not dockerfile_path:
        crit_error("Dockerfile path cannot be empty.")
        sys.exit(1)

    for out_file in outputs:
        if not out_file:
            crit_error("output file path cannot be empty.")
            sys.exit(1)

    if install_as is not None and not install_as:
        crit_error("--install-as value cannot be empty.")
        sys.exit(1)

    install_as = install_as or ""
    no_cache = bool(getattr(args, "no_cache", False))
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

    if not os.path.isdir(build_dir):
        crit_error(f"build context '{build_dir}' is not a directory.")
        sys.exit(1)

    if dockerfile != "-" and not os.path.isfile(dockerfile):
        crit_error(f"required file '{dockerfile}' does not exist.")
        sys.exit(1)

    # ----- read + parse Dockerfile -----
    try:
        if dockerfile == "-":
            text = sys.stdin.read()
        else:
            with open(dockerfile, "rb") as fh:
                text = fh.read().decode("utf-8", errors="replace")
    except OSError as exc:
        crit_error(f"cannot read Dockerfile: {exc}")
        sys.exit(1)

    try:
        _directives, instructions = parse_dockerfile(text)
    except DockerfileSyntaxError as exc:
        crit_error(f"syntax error in Dockerfile: {exc}")
        sys.exit(1)

    if not instructions:
        crit_error("no instructions in Dockerfile.")
        sys.exit(1)

    # ----- proot gate -----
    # `build` is exempt from the CLI's startup proot probe because a
    # pure-metadata / COPY-only Dockerfile never needs proot. Now that
    # the Dockerfile is parsed, refuse (and, on Termux, offer to install
    # proot) only when a RUN-family instruction is actually present.
    if needs_proot(instructions):
        from proot_distro.cli import ensure_proot_installed
        ensure_proot_installed()

    # ----- target architecture -----
    if override_arch:
        target_arch = normalize_arch(override_arch)
        if target_arch is None:
            crit_error(f"unknown architecture '{override_arch}'.")
            sys.exit(1)
    else:
        target_arch = get_device_cpu_arch()

    # ----- Validate install-as container name -----
    if install_as:
        require_valid_name(install_as, kind="--install-as value")

        if container_is_installed(install_as):
            crit_error(
                f"container '{install_as}' defined by --install-as already "
                f"exists. Use '{PROGRAM_NAME} remove {install_as}' first or "
                f"'{PROGRAM_NAME} reset {install_as}' to rebuild."
            )
            sys.exit(1)

    # ----- determine the canonical tag set -----
    if not tags:
        derived = _derive_tag_from_path(build_dir, dockerfile)
        if not derived:
            crit_error(
                "cannot derive a tag from the build path. Pass '--tag' "
                "explicitly (e.g. --tag myapp:latest)."
            )
            sys.exit(1)
        tags = [derived]

    for t in tags:
        if not _is_valid_tag(t):
            crit_error(
                f"tag '{t}' is not valid. A tag must start with an "
                f"alphanumeric character and contain only letters, "
                f"digits, underscores, dots, hyphens, slashes, or a "
                f"single colon for the version."
            )
            sys.exit(1)

    tags = [with_explicit_tag(t) for t in tags]
    primary_tag = tags[0]

    # ----- refuse to overwrite existing output files -----
    for out_file in outputs:
        out_abs = os.path.abspath(os.path.expanduser(out_file))
        if os.path.exists(out_abs):
            crit_error(
                f"file '{out_abs}' already exists. "
                f"Please specify a different name."
            )
            sys.exit(1)

    # Acquire one exclusive BuildLock per tag for the duration of the
    # build. Sorted by lock path so two concurrent builds with
    # overlapping but differently-ordered tag sets can't deadlock.
    build_locks = sorted(
        [BuildLock(t, target_arch, command="build") for t in tags],
        key=lambda l: l.lock_path,
    )

    with ExitStack() as lock_stack:
        for lock in build_locks:
            lock_stack.enter_context(lock)

        # ----- run the build -----
        tmp_root, tmp_root_fd = _make_build_tmp()

        # Single try/except/finally covers the whole post-setup work so
        # KeyboardInterrupt during any phase (engine, cache write, OCI
        # archive write, install-as) is reported cleanly and tmp_root is
        # always removed. The engine is built inside it because it owns
        # the stage descriptors, which have to be released even if it
        # never gets as far as running.
        engine = None
        try:
            engine = BuildEngine(
                build_dir=build_dir,
                tmp_root=tmp_root,
                tmp_root_fd=tmp_root_fd,
                target_arch_pd=target_arch,
                user_build_args=build_args,
                target_stage=target_stage,
                verbose=verbose,
                quiet=quiet,
                no_cache=no_cache,
                emulator=emulator,
            )
            try:
                final_stage = engine.run(instructions)
            except BuildError as exc:
                # A BuildError interpolates names raw, and the ones it
                # reports on come from the image or from an ADD'd archive
                # (see copy_step._materialise_files).
                log_error(f"Build failed: {quote_error(exc)}")
                sys.exit(1)
            except OSError as exc:
                # The engine's own walks report per-entry failures and
                # carry on; one that reaches here is the walk losing its
                # footing — a directory moved out from under it while a
                # step's leftovers were still running (dirfd.Levels).
                log_error(f"Build failed: {quote_error(exc)}")
                sys.exit(1)

            # ----- assemble manifest + image_config -----
            arch_docker = ARCH_TO_DOCKER.get(target_arch, (target_arch, ""))[0]
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
                    log_error(f"Cannot write manifest cache for '{t}': {exc}")
                    sys.exit(1)

            # OCI tarball outputs.
            for out_file in outputs:
                out_abs = os.path.abspath(os.path.expanduser(out_file))
                try:
                    if not quiet:
                        log_info(f"Writing OCI archive to '{out_abs}'...")
                    write_oci_archive(out_abs, manifest, image_config, primary_tag)
                except (OSError, RuntimeError) as exc:
                    log_error(f"Cannot write '{out_file}': {exc}")
                    sys.exit(1)

            # Build summary.
            if not quiet:
                total_size = sum(l["size"] for l in final_stage.layers)
                log_info("Build complete.")
                msg()
                msg(f"{C['CYAN']}Tag(s): "
                    f"{C['GREEN']}{', '.join(tags)}{C['RST']}")
                msg(f"{C['CYAN']}Layers: "
                    f"{C['GREEN']}{len(final_stage.layers)}"
                    f" ({fmt_size(total_size)} total){C['RST']}")
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
            log_error("Aborted by user.")
            sys.exit(1)
        finally:
            # Stage rootfs trees are assembled from images and mutated by
            # RUN steps, so their depth and their modes are not ours to
            # assume. shutil.rmtree(ignore_errors=True) swallowed an
            # OSError but not the RecursionError a deep one raised, and
            # could not chmod its way into a sealed directory either.
            # The parent is walked down to for the same reason it was
            # walked down to when the scratch root was created.
            #
            # The stages' descriptors go with it: they are what every
            # host-side step addressed the tree through, and they are
            # this build's for exactly as long as the tree is.
            if engine is not None:
                engine.close()
            try:
                os.close(tmp_root_fd)
            except OSError:
                pass
            statedir.remove_state_tree(tmp_root)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _make_build_tmp():
    """Create the scratch root a build assembles its stages in. (path, fd).

    RUNTIME_DIR/build-tmp is a predictable name inside the state tree, and
    tempfile.mkdtemp(dir=...) resolves it: a guest that left
    `build-tmp -> <host dir>` behind -- the runtime tree is under the
    $TERMUX_PREFIX bound read-write into every non-isolated container --
    had every stage rootfs, every spooled ADD and every packed layer
    assembled inside that host directory, and the cleanup at the end of
    the build remove what it found there. The directory is walked down to
    with O_NOFOLLOW and the scratch root created with mkdirat off the
    descriptor that walk validated; what is made *inside* it is this
    process's own, since the name is fresh and the mode is 0700.

    A runtime tree that cannot hold one falls back to the system
    temporary directory, which is what the previous `build_tmp = None`
    did.

    The descriptor comes back with the path because the *contents* of
    the scratch root are named too: each stage's directory and rootfs,
    the ADD spool, a COPY --from image's throwaway tree. Composing those
    onto the path and opening them left every one of them re-resolvable
    by anything running as the invoking user -- which is what a RUN
    step's leftovers are. The caller owns the descriptor and closes it
    when the run is over.
    """
    build_tmp = os.path.join(RUNTIME_DIR, "build-tmp")
    try:
        dir_fd = statedir.open_state_dir(build_tmp, create=True)
    except OSError:
        return _fallback_build_tmp()
    try:
        name = f"pd-build-{os.getpid()}.{os.urandom(4).hex()}"
        os.mkdir(name, 0o700, dir_fd=dir_fd)
        # Off the descriptor that walk validated, so what comes back
        # names the directory just created and not whatever the name
        # leads to by the time anything under it is used.
        run_fd = dirfd.opendir_at(dir_fd, name)
    except OSError:
        return _fallback_build_tmp()
    finally:
        os.close(dir_fd)
    return os.path.join(build_tmp, name), run_fd


def _fallback_build_tmp():
    """The scratch root in the system temp dir, when the state tree fails.

    mkdtemp() makes a fresh 0700 directory of its own, so opening it by
    name is opening what this process just created.
    """
    path = tempfile.mkdtemp(prefix="pd-build-")
    try:
        return path, dirfd.opendir(path)
    except OSError:
        shutil.rmtree(path, ignore_errors=True)
        raise


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
    if not base or not is_valid_name(base):
        return ""
    return f"{base}:latest"


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
    return is_valid_name(last)


def _install_as_container(install_name, image_ref, target_arch, quiet):
    """Run the install command for `image_ref` aliased as `install_name`."""
    if not quiet:
        log_info(f"Installing built image as '{install_name}'...")

    command_install(
        SimpleNamespace(
            image_ref=image_ref,
            custom_container_name=install_name,
            override_arch=target_arch,
        )
    )
