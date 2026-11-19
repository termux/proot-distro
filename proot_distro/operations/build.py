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

# Architecture: Shared orchestration for Dockerfile builds.
# Steps: load the Dockerfile (file or stdin), parse it, decide whether
# proot is required for this particular Dockerfile (only RUN family
# instructions need it), run the build engine, then write the
# requested output variants (manifest cache always; OCI tarball per
# --output; container install per --install-as).

import errno
import fcntl
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from contextlib import ExitStack

from proot_distro import dirfd, statedir
from proot_distro.constants import RUNTIME_DIR
from proot_distro.paths import container_is_installed
from proot_distro.message import quote_error, quote_path
from proot_distro.locking import BuildLock
from proot_distro.arch import get_device_cpu_arch, normalize_arch
from proot_distro.helpers.dockerfile import (
    DockerfileSyntaxError,
    MAX_DOCKERFILE_BYTES,
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
    write_oci_archive_fileobj,
)
from proot_distro.helpers.docker import (
    ARCH_TO_DOCKER, InvalidReference, parse_tag_reference, with_explicit_tag,
)
from proot_distro.names import is_valid_name
from proot_distro.names import NAME_RULE_HINT
from proot_distro.operation_errors import (
    AlreadyExistsFailure,
    NotFoundFailure,
    OperationFailure,
)
from proot_distro.operations.context import OperationContext
from proot_distro.operations.errors import (
    BuildValidationFailure,
    build_failure,
)
from proot_distro.operations.install import install_container
from proot_distro.operations.inventory import list_images
from proot_distro.operations.locks import operation_lock
from proot_distro.operations.models import ImageBuild


# ---------------------------------------------------------------------------
# Top-level service
# ---------------------------------------------------------------------------

def _read_dockerfile(dockerfile: str, fileobj=None, *, limit=None):
    """The Dockerfile's bytes, or None when there are more than the cap.

    "-" is stdin, read as bytes like the file so both decode the same
    way (the parser normalises line endings itself). One byte more than
    MAX_DOCKERFILE_BYTES is read and its presence is the refusal, so the
    bound is on what arrives rather than on a size the file declares.
    """
    limit = MAX_DOCKERFILE_BYTES if limit is None else limit
    if fileobj is not None:
        data = fileobj.read(limit + 1)
    else:
        with open(dockerfile, "rb") as fh:
            data = fh.read(limit + 1)
    if len(data) > limit:
        return None
    return data


def build_image(
    build_path,
    *,
    dockerfile_path=None,
    dockerfile_file=None,
    tags=(),
    build_args=None,
    architecture=None,
    target_stage=None,
    emulator=None,
    outputs=(),
    install_as=None,
    no_cache=False,
    verbose=False,
    quiet=False,
    build_output_fd=None,
    ensure_proot=None,
    on_built=None,
    dockerfile_limit=None,
    context=None,
) -> ImageBuild:
    """Build and cache an OCI image from a Dockerfile and context tree."""
    context = context if context is not None else OperationContext()
    build_args = {} if build_args is None else build_args
    try:
        build_path = os.fspath(build_path)
    except TypeError:
        build_path = None
    if not isinstance(build_path, str) or not build_path:
        raise BuildValidationFailure("build context path cannot be empty.")
    if dockerfile_path is not None:
        try:
            dockerfile_path = os.fspath(dockerfile_path)
        except TypeError:
            dockerfile_path = None
        if not isinstance(dockerfile_path, str) or not dockerfile_path:
            raise BuildValidationFailure("Dockerfile path cannot be empty.")
    if not isinstance(build_args, Mapping) or not all(
            isinstance(key, str) and key and isinstance(value, str)
            for key, value in build_args.items()):
        raise BuildValidationFailure(
            "build arguments must map non-empty strings to strings."
        )
    if not isinstance(no_cache, bool):
        raise BuildValidationFailure("no_cache must be a boolean.")
    if architecture is not None and not isinstance(architecture, str):
        raise BuildValidationFailure(
            "architecture must be a string or None."
        )
    if target_stage is not None and (
            not isinstance(target_stage, str) or not target_stage):
        raise BuildValidationFailure(
            "target stage must be a non-empty string or None."
        )
    if emulator is not None:
        try:
            emulator = os.fspath(emulator)
        except TypeError:
            emulator = None
        if not isinstance(emulator, str):
            raise BuildValidationFailure(
                "emulator must be a text path or None."
            )
    if ensure_proot is not None and not callable(ensure_proot):
        raise BuildValidationFailure("ensure_proot must be callable or None.")
    if on_built is not None and not callable(on_built):
        raise BuildValidationFailure("on_built must be callable or None.")
    if dockerfile_limit is not None and (
            isinstance(dockerfile_limit, bool)
            or not isinstance(dockerfile_limit, int)
            or dockerfile_limit < 1):
        raise BuildValidationFailure(
            "Dockerfile size limit must be a positive integer."
        )

    build_dir = os.path.abspath(os.path.expanduser(build_path))
    if dockerfile_path is None:
        dockerfile = os.path.join(build_dir, "Dockerfile")
    elif dockerfile_path == "-":
        dockerfile = "-"
    else:
        dockerfile = os.path.abspath(os.path.expanduser(dockerfile_path))
    if not os.path.isdir(build_dir):
        raise NotFoundFailure(
            f"build context '{quote_path(build_dir)}' is not a directory."
        )
    if dockerfile_file is None and not os.path.isfile(dockerfile):
        raise NotFoundFailure(
            f"required file '{quote_path(dockerfile)}' does not exist."
        )

    limit = MAX_DOCKERFILE_BYTES if dockerfile_limit is None else dockerfile_limit
    try:
        raw = _read_dockerfile(dockerfile, dockerfile_file, limit=limit)
    except OSError as exc:
        raise OperationFailure(
            f"cannot read Dockerfile: {quote_error(exc)}"
        ) from exc
    if raw is None:
        raise BuildValidationFailure(
            f"Dockerfile is larger than {limit} bytes; refusing to read it."
        )
    if not isinstance(raw, bytes):
        raise BuildValidationFailure(
            "Dockerfile stream must return bytes."
        )
    try:
        _directives, instructions = parse_dockerfile(
            raw.decode("utf-8", errors="replace")
        )
    except DockerfileSyntaxError as exc:
        raise BuildValidationFailure(
            f"syntax error in Dockerfile: {quote_error(exc)}"
        ) from exc
    if not instructions:
        raise BuildValidationFailure("no instructions in Dockerfile.")
    if needs_proot(instructions) and ensure_proot is not None:
        ensure_proot()

    if architecture:
        target_arch = normalize_arch(architecture)
        if target_arch is None:
            raise BuildValidationFailure(
                f"unknown architecture '{quote_path(str(architecture))}'."
            )
    else:
        target_arch = get_device_cpu_arch()

    if install_as is not None:
        if not isinstance(install_as, str) or not install_as:
            raise BuildValidationFailure("--install-as value cannot be empty.")
        if not is_valid_name(install_as):
            raise BuildValidationFailure(
                f"--install-as value '{quote_path(install_as)}' is not "
                f"valid. {NAME_RULE_HINT}"
            )
        if container_is_installed(install_as, raise_errors=True):
            raise AlreadyExistsFailure(
                f"container '{quote_path(install_as)}' defined by "
                f"--install-as already exists."
            )

    if isinstance(tags, str):
        tags = [tags]
    else:
        try:
            tags = list(tags)
        except TypeError:
            raise BuildValidationFailure(
                "tags must be an iterable of strings."
            ) from None
    if not tags:
        derived = _derive_tag_from_path(build_dir, dockerfile)
        if not derived:
            raise BuildValidationFailure(
                "cannot derive a tag from the build path. Pass '--tag' "
                "explicitly (e.g. --tag myapp:latest)."
            )
        tags = [derived]
    for tag in tags:
        if not isinstance(tag, str):
            raise BuildValidationFailure("every build tag must be a string.")
        try:
            parse_tag_reference(tag)
        except InvalidReference as exc:
            raise BuildValidationFailure(
                f"tag '{quote_path(tag)}' is not valid: {quote_error(exc)}."
            ) from exc
    tags = [with_explicit_tag(tag) for tag in tags]

    if isinstance(outputs, (str, os.PathLike)) or callable(
            getattr(outputs, "write", None)):
        outputs = [outputs]
    else:
        try:
            outputs = list(outputs)
        except TypeError:
            raise BuildValidationFailure(
                "outputs must be an iterable of paths or binary streams."
            ) from None
    resolved_outputs = []
    for output in outputs:
        if isinstance(output, (str, os.PathLike)):
            try:
                output = os.fspath(output)
            except TypeError:
                output = None
        if isinstance(output, str):
            if not output:
                raise BuildValidationFailure("output file path cannot be empty.")
            output = os.path.abspath(os.path.expanduser(output))
            if os.path.lexists(output):
                raise AlreadyExistsFailure(
                    f"file '{quote_path(output)}' already exists. Please "
                    f"specify a different name."
                )
            resolved_outputs.append(output)
        elif callable(getattr(output, "write", None)):
            resolved_outputs.append(output)
        else:
            raise BuildValidationFailure(
                "every output must be a path or writable binary stream."
            )

    build_locks = sorted(
        [BuildLock(tag, target_arch, command="build") for tag in tags],
        key=lambda lock: lock.lock_path,
    )
    with ExitStack() as lock_stack:
        for lock in build_locks:
            lock_stack.enter_context(operation_lock(lock))
        return _run_build(
            build_dir=build_dir,
            instructions=instructions,
            tags=tags,
            build_args=build_args,
            target_arch=target_arch,
            target_stage=target_stage,
            emulator=emulator or "",
            outputs=resolved_outputs,
            install_as=install_as,
            no_cache=no_cache,
            verbose=verbose,
            quiet=quiet,
            build_output_fd=build_output_fd,
            on_built=on_built,
            context=context,
        )


def _run_build(
    *, build_dir, instructions, tags, build_args, target_arch, target_stage,
    emulator, outputs, install_as, no_cache, verbose, quiet,
    build_output_fd, on_built, context,
):
    """Execute a validated build while its tag locks are held."""
    _reclaim_build_tmp(lambda count: context.info(
        f"Removing scratch left by {count} interrupted build(s)...",
        phase="build",
    ))
    tmp_root, tmp_root_fd = _make_build_tmp()
    engine = None
    interrupted = False
    try:
        try:
            engine = BuildEngine(
                build_dir=build_dir,
                tmp_root=tmp_root,
                tmp_root_fd=tmp_root_fd,
                target_arch_pd=target_arch,
                user_build_args=dict(build_args),
                target_stage=target_stage,
                verbose=verbose,
                quiet=quiet,
                no_cache=no_cache,
                emulator=emulator,
            )
        except (BuildError, OSError) as exc:
            raise build_failure(
                exc, f"Build failed: {quote_error(exc)}"
            ) from exc
        engine.api_output_fd = build_output_fd
        try:
            final_stage = engine.run(instructions)
        except (BuildError, OSError) as exc:
            raise build_failure(
                exc, f"Build failed: {quote_error(exc)}"
            ) from exc

        arch_docker = ARCH_TO_DOCKER.get(target_arch, (target_arch, ""))[0]
        manifest, image_config = build_manifest_and_config(
            final_stage.image_config, final_stage.layers, arch_docker,
        )
        for tag in tags:
            try:
                store_in_cache(tag, target_arch, manifest, image_config)
            except OSError as exc:
                raise OperationFailure(
                    f"Cannot write manifest cache for "
                    f"'{quote_path(tag)}': {quote_error(exc)}"
                ) from exc

        primary_tag = tags[0]
        output_results = []
        for output in outputs:
            if isinstance(output, str):
                context.info(
                    f"Writing OCI archive to '{quote_path(output)}'...",
                    phase="build-output", resource=output,
                )
                try:
                    write_oci_archive(
                        output, manifest, image_config, primary_tag
                    )
                except (OSError, RuntimeError) as exc:
                    raise OperationFailure(
                        f"Cannot write '{quote_path(output)}': "
                        f"{quote_error(exc)}"
                    ) from exc
                output_results.append(output)
            else:
                try:
                    write_oci_archive_fileobj(
                        output, manifest, image_config, primary_tag
                    )
                except (OSError, RuntimeError) as exc:
                    raise OperationFailure(
                        f"Cannot write OCI archive stream: {quote_error(exc)}"
                    ) from exc
                output_results.append(None)

        total_size = sum(layer["size"] for layer in final_stage.layers)
        context.info(
            "Build complete.", phase="build", resource=primary_tag
        )
        if on_built is not None:
            on_built(tuple(tags), len(final_stage.layers), total_size)

        installed = None
        if install_as:
            context.info(
                f"Installing built image as '{install_as}'...",
                phase="build-install", resource=install_as,
            )
            installed = install_container(
                primary_tag,
                name=install_as,
                architecture=target_arch,
                context=context,
            )

        inventory = list_images()
        images = []
        for tag in tags:
            image = next((
                item for item in inventory
                if item.reference == tag
                and item.architecture == target_arch
            ), None)
            if image is None:
                raise OperationFailure(
                    f"build completed without publishing tag '{tag}'"
                )
            images.append(image)
        return ImageBuild(
            images=tuple(images),
            outputs=tuple(output_results),
            installed_container=installed,
            layer_count=len(final_stage.layers),
            size_bytes=total_size,
        )
    except KeyboardInterrupt:
        interrupted = True
        raise
    finally:
        _discard_build_tmp(
            engine, tmp_root, tmp_root_fd, interrupted=interrupted
        )


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

    The descriptor also holds the lock that marks the root in use, and
    the name carries this process's ID: the two things
    _reclaim_build_tmp() checks before it removes a root as one a build
    left behind.
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
    # Between the mkdir and this, the root is unlocked; the process ID
    # in its name, ours and alive, is what keeps a sweep off it then.
    _lock_scratch(run_fd)
    return os.path.join(build_tmp, name), run_fd


# The names _make_build_tmp() gives: the creating process's ID, then
# four random bytes. Nothing else in build-tmp/ is the sweep's to touch.
_SCRATCH_NAME = re.compile(r"pd-build-([0-9]{1,10})\.[0-9a-f]{8}")


def _lock_scratch(fd: int) -> bool:
    """Take the lock that marks a scratch root in use. False if held.

    flock(2) on the directory itself, exclusive and non-blocking. The
    kernel drops it with the last descriptor of that open, so a build
    process killed outright lets go of its root too. A filesystem that
    cannot hold the lock answers as if it were free, the rule
    locking.py applies; the process check in _reclaim_build_tmp() is
    what keeps a running build's root then.
    """
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError as exc:
        return exc.errno not in (errno.EAGAIN, errno.EACCES)
    return True


def _process_exists(pid: int) -> bool:
    """True unless *pid* is known to name no process.

    One there but not ours to signal still exists, and so does anything
    that cannot be asked about: when in doubt, a root is kept.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except (OSError, OverflowError, ValueError):
        return True
    return True


def _reclaim_build_tmp(announce=None) -> int:
    """Remove the scratch roots of builds that are no longer running.

    A build removes its own root when it ends, but a build process
    killed outright never gets to, and an interrupt pressed twice stops
    that cleanup (see _discard_build_tmp). Nothing else ever removed
    one, and a root holds the rootfs of every stage built in it.

    A root is taken for left behind only when its name is one
    _make_build_tmp() gives, the process that name carries is gone, and
    its lock is free. The process check covers the moment before the
    lock is taken and builds of a version that took none; the lock
    covers a process ID that cannot be seen from here and a root renamed
    after the fact. The lock is kept while the root is removed, so two
    builds starting together do not both work on one. Entries are
    opened O_NOFOLLOW and removed by rmtree_at(), which unlinks a link
    instead of following it: build-tmp/ is guest-writable on Termux,
    and what is planted there is removed at most, never reached through.

    *announce(count)* is called before anything is removed, which can
    take a while. Returns how many roots were removed; a failure to look
    or to remove is never the build's.
    """
    build_tmp = os.path.join(RUNTIME_DIR, "build-tmp")
    try:
        dir_fd = statedir.open_state_dir(build_tmp)
    except OSError:
        return 0
    claimed = []
    try:
        try:
            names = os.listdir(dir_fd)
        except OSError:
            return 0
        for name in sorted(names):
            match = _SCRATCH_NAME.fullmatch(name)
            if match is None or _process_exists(int(match.group(1))):
                continue
            try:
                fd = dirfd.opendir_at(dir_fd, name)
            except OSError:
                continue
            if not _lock_scratch(fd):
                os.close(fd)
                continue
            claimed.append((name, fd))
        if not claimed:
            return 0
        if announce is not None:
            announce(len(claimed))
        for name, _fd in claimed:
            dirfd.rmtree_at(
                dir_fd, name, force=True, on_error=lambda _rel, _exc: None
            )
        return len(claimed)
    finally:
        for _name, fd in claimed:
            os.close(fd)
        os.close(dir_fd)


def _discard_build_tmp(engine, tmp_root, tmp_root_fd, *, interrupted):
    """Close the engine and remove the scratch root, interrupted or not.

    The build's last step, and the one an interrupt that comes late
    lands in: removing a large root takes a while, and the work it
    follows is already done. One landing here cut the cleanup short and
    stranded the root. So the first interrupt lets the cleanup run to
    the end and is raised after it; a second stops it where it stands,
    and _reclaim_build_tmp() takes the rest when the next build starts.
    *interrupted* says the build is already unwinding from the first,
    which makes one arriving here the second.

    The root's descriptor, which holds its lock, is closed last, once
    the tree is gone.
    """
    def cleanup():
        if engine is not None:
            engine.close()
        statedir.remove_state_tree(tmp_root)

    try:
        if interrupted:
            cleanup()
        else:
            try:
                cleanup()
            except KeyboardInterrupt:
                cleanup()
                raise
    finally:
        try:
            os.close(tmp_root_fd)
        except OSError:
            pass


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


def _derive_tag_from_path(build_dir, dockerfile):
    """Pick a default tag based on the build context basename.

    Made to fit the reference grammar rather than merely checked against
    it: lowercase, anything outside [a-z0-9._-] turned into '-', a run of
    separators Docker does not allow ('._', '_-', '___') collapsed into one
    '-' as runs of '-' always were, and none left at either end. A
    directory called `app_` or `my._app` used to produce a tag no registry
    would take.
    """
    base = os.path.basename(os.path.abspath(build_dir).rstrip("/"))
    if not base or base in (".", ".."):
        if dockerfile and dockerfile != "-":
            base = os.path.basename(os.path.dirname(os.path.abspath(dockerfile)))
    base = re.sub(r"[^a-z0-9_.\-]", "-", base.lower())
    base = re.sub(
        r"[._-]+",
        lambda m: (m.group() if re.fullmatch(r"[._]|__|-+", m.group())
                   else "-"),
        base,
    )
    base = re.sub(r"-+", "-", base).strip("._-")
    tag = f"{base}:latest"
    try:
        parse_tag_reference(tag)
    except InvalidReference:
        return ""
    return tag if is_valid_name(base) else ""


__all__ = (
    "_derive_tag_from_path",
    "_make_build_tmp",
    "build_image",
)
