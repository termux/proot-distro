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

# Architecture: Top-level `install` command.
#
#   command_install   — argument validation, source-type dispatch, lock,
#                       and final summary.
#   _run_install      — the actual install pipeline (held under the
#                       container's exclusive lock).
#
# Heavy lifting lives in sibling modules: pulls go through
# helpers.docker.pull_image, local archives through install_local.

import json
import os
import re
import stat
import tarfile

from proot_distro import dirfd, statedir
from proot_distro.atomic import atomic_write
from proot_distro.constants import BASE_CACHE_DIR
from proot_distro.message import quote_path
from proot_distro.operation_errors import (
    AlreadyExistsFailure,
    IntegrityFailure,
    NotFoundFailure,
    OperationFailure,
)
from proot_distro.operations.context import OperationContext
from proot_distro.operations.errors import InstallValidationFailure
from proot_distro.operations.locks import operation_lock
from proot_distro.operations.models import ContainerInstallation
from proot_distro.progress import clear_bar
from proot_distro.locking import ContainerLock
from proot_distro.arch import get_device_cpu_arch, normalize_arch
from proot_distro.names import NAME_RULE_HINT, is_valid_name
from proot_distro.paths import (
    container_dir, container_manifest, container_rootfs,
    container_is_installed, open_container_pair,
)
from proot_distro.sysdata import setup_fake_sysdata
from proot_distro.helpers.docker import (
    InvalidReference, derive_alias, parse_reference, pull_image,
)
from proot_distro.helpers.rootfs import (
    open_etc,
    register_android_ids_at,
    write_hosts_at,
    write_resolv_conf_at,
)
from proot_distro.helpers.download import download_file
from proot_distro.operations.install_local import install_from_local_file


# Archive extensions stripped when deriving a container name from a filename.
_ARCHIVE_EXTS = (
    ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
    ".oci.tar.xz", ".oci.tar.gz", ".oci.tar.zst", ".oci.tar",
    ".tar.lzma", ".tlzma", ".tar.zst", ".tzst", ".tar",
)


def _is_local_path(ref: str) -> bool:
    """Return True if ref should be treated as a local file path.

    A reference is local only when it has an explicit path syntax: a
    leading '/', './', '../', or '~'. A bare token like 'ubuntu' is
    treated as a Docker image even if a file with that name happens
    to exist in the cwd.
    """
    return ref.startswith(("/", "./", "../", "~"))


def _is_url(ref: str) -> bool:
    """Return True if ref is an HTTP/HTTPS URL."""
    return ref.startswith(("http://", "https://"))


def _derive_local_name(path: str) -> str:
    """Derive a container alias from an archive filename.

    Returns an empty string if a valid name cannot be derived.
    """
    base = os.path.basename(path)
    low = base.lower()
    for ext in _ARCHIVE_EXTS:
        if low.endswith(ext):
            base = base[:-len(ext)]
            break
    base = re.sub(r"[^a-z0-9_.\-]", "-", base.lower())
    base = re.sub(r"^[^a-z0-9]+", "", base)
    base = re.sub(r"-{2,}", "-", base).strip("-")
    return base


def _cache_temp_file(prefix: str) -> str:
    """Create an empty temporary in the download cache. Returns its path.

    tempfile.mkstemp(dir=BASE_CACHE_DIR) resolved that name, and the
    cache root is a name a guest can re-point on Termux, so the whole
    downloaded archive landed in whatever directory it led to. The
    directory is walked down to instead and the file created O_EXCL off
    the descriptor the walk validated.

    Nothing writes *through* this name: it is only the destination
    download_file() hands to atomic_replace(), which writes its own
    temporary through a descriptor and renames it onto this entry --
    and rename(2) replaces a symlink standing at the destination rather
    than following it. What the name buys is a reservation, so two
    installs running at once cannot pick the same one.
    """
    dir_fd = statedir.open_state_dir(BASE_CACHE_DIR, create=True)
    try:
        name = dirfd.temp_name(
            prefix, f".{os.getpid()}.{os.urandom(4).hex()}.tmp",
        )
        os.close(dirfd.open_new_at(dir_fd, name, 0o600)[0])
        return os.path.join(BASE_CACHE_DIR, name)
    finally:
        os.close(dir_fd)


# ---------------------------------------------------------------------------
# Command
# ---------------------------------------------------------------------------

def install_container(
    image_ref,
    *,
    name=None,
    architecture=None,
    allow_insecure=False,
    context=None,
) -> ContainerInstallation:
    """Install a container from an OCI reference, URL, or local archive."""
    context = context if context is not None else OperationContext()
    if not isinstance(image_ref, str) or not image_ref:
        raise InstallValidationFailure(
            "install source must be a non-empty string"
        )
    if name is not None:
        if not name:
            raise InstallValidationFailure("container name can't be empty.")
        if not isinstance(name, str) or not is_valid_name(name):
            raise InstallValidationFailure(
                f"container name '{name}' is not valid. {NAME_RULE_HINT}"
            )
    if not isinstance(allow_insecure, bool):
        raise InstallValidationFailure("allow_insecure must be a boolean")

    device_arch = get_device_cpu_arch()
    raw_arch = architecture
    if raw_arch:
        dist_arch = normalize_arch(raw_arch)
        if dist_arch is None:
            raise InstallValidationFailure(
                f"unknown architecture '{raw_arch}'. Valid values: "
                f"aarch64, arm, i686, riscv64, x86_64 (or Docker format: "
                f"linux/arm64, linux/amd64, linux/arm/v7, linux/386, "
                f"linux/riscv64)."
            )
    else:
        dist_arch = device_arch

    # Decide between local-file, URL, and Docker-pull mode.
    local_path = os.path.expanduser(image_ref) if _is_local_path(image_ref) else None
    url = image_ref if _is_url(image_ref) else None

    # Anything else is an image reference, and one Docker would refuse is
    # refused here, before a container name is derived from it or a lock
    # taken: `ubuntu@sha256:<hex>` used to surface as "cannot derive a
    # valid container name", and `Ubuntu` as whatever the registry said
    # about a repository no client can name.
    if local_path is None and url is None:
        try:
            parse_reference(image_ref)
        except InvalidReference as exc:
            raise InstallValidationFailure(
                f"cannot install '{quote_path(image_ref)}': {exc}."
            ) from exc

    install_name = _resolve_install_name(
        image_ref, local_path, url, name,
    )

    lock = ContainerLock(install_name, exclusive=True, command="install")
    with operation_lock(lock):
        return _run_install(
            install_name, image_ref, local_path, url, dist_arch,
            allow_insecure, context,
        )


def _resolve_install_name(image_ref, local_path, url, custom_container_name):
    """Decide the on-disk container name or raise a typed failure."""
    if local_path is not None:
        if not os.path.isfile(local_path):
            raise NotFoundFailure(
                f"local file '{local_path}' does not exist or is not a "
                f"regular file."
            )
        if custom_container_name:
            return custom_container_name
        derived = _derive_local_name(local_path)
        if not derived or not is_valid_name(derived):
            raise InstallValidationFailure(
                f"cannot determine a valid container name from "
                f"'{os.path.basename(local_path)}'. Specify the name with "
                f"'--name NAME'."
            )
        return derived

    if url is not None:
        if custom_container_name:
            return custom_container_name
        url_path = url.split("?")[0].split("#")[0]
        derived = _derive_local_name(url_path)
        if not derived or not is_valid_name(derived):
            raise InstallValidationFailure(
                f"cannot determine a valid container name from '{url}'. "
                f"Specify the name with '--name NAME'."
            )
        return derived

    derived = custom_container_name if custom_container_name else derive_alias(image_ref)
    if not is_valid_name(derived):
        raise InstallValidationFailure(
            f"cannot derive a valid container name from '{image_ref}'. "
            f"Specify the name with '--name NAME'."
        )
    return derived


def _is_regular_at(dir_fd: int, name: str) -> bool:
    """True when *name* under dir_fd is a plain file, following nothing."""
    try:
        st = dirfd.lstat_at(dir_fd, name)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode)


def _run_install(
    install_name: str,
    image_ref: str,
    local_path,
    url,
    dist_arch: str,
    allow_insecure: bool = False,
    context=None,
) -> ContainerInstallation:
    """Inner install logic — called with the container lock already held."""
    context = context if context is not None else OperationContext()
    container_path = container_dir(install_name)
    rootfs_dir = container_rootfs(install_name)

    # Walked down to, not named: `containers/<name>` is guest-writable on
    # Termux, and os.path.isdir() said "no" to a symlink pointing at a
    # host directory with no rootfs in it, after which the whole install
    # was extracted inside that directory. A planted component stops the
    # command in open_container_pair(); what reaches here is either a
    # real rootfs (refuse, the container exists) or nothing yet.
    if container_is_installed(install_name, raise_errors=True):
        failure = AlreadyExistsFailure(
            f"container '{install_name}' already exists. Specify a "
            f"different name with '--name NAME'."
        )
        failure.name = install_name
        raise failure

    if local_path is not None:
        context.info(
            f"Installing from '{os.path.basename(local_path)}' as "
            f"'{install_name}'...",
            phase="install", resource=install_name,
        )
    elif url is not None:
        context.info(
            f"Installing from URL '{url}' as '{install_name}'...",
            phase="install", resource=install_name,
        )
    else:
        last_component = image_ref.split("/")[-1]
        display_ref = (
            image_ref if ":" in last_component else f"{image_ref}:latest"
        )
        context.info(
            f"Installing '{display_ref}' as '{install_name}'...",
            phase="install", resource=install_name,
        )

    # Made level by level off the descriptor of the level above, for the
    # same reason it is checked that way -- and *kept*: the extraction
    # writes every member as (dir_fd, name) beneath this descriptor, so
    # nothing between the check and the last byte written resolves
    # containers/<name>/rootfs a second time. Closing it and handing the
    # path down instead left the whole unpack open to a session that
    # re-pointed the name, which lands the image (and the manifest that
    # follows it) in a host directory of the session's choosing. The
    # container directory comes back with it, for the sysdata stubs
    # written beside the rootfs at the end.
    container_fd, rootfs_fd = open_container_pair(
        install_name, create=True, raise_errors=True
    )

    def _cleanup() -> None:
        # The tree being discarded is whatever the image or the archive
        # produced, so it can be deeper than the interpreter's stack and
        # can hold directories the image sealed. shutil.rmtree() managed
        # neither: it recursed, and RecursionError is not an OSError, so
        # the handler here let it past and the failed install took the
        # command down with it instead of cleaning up. The walk reaches
        # `containers` the same way everything else here does, so a
        # planted parent cannot aim the removal at a host directory.
        statedir.remove_state_tree(container_path)

    tmp_archive = None
    try:
        if local_path is not None:
            context.info(
                "Extracting rootfs from archive...",
                phase="install", resource=install_name,
            )
            metadata = install_from_local_file(
                local_path, rootfs_fd, dist_arch, context,
            )
        elif url is not None:
            tmp_archive = _cache_temp_file(f"dl_install_{install_name}")
            context.info(
                "Downloading archive...",
                phase="download", resource=url,
            )
            download_file(url, tmp_archive, insecure=allow_insecure)
            context.info(
                "Extracting rootfs from archive...",
                phase="install", resource=install_name,
            )
            metadata = install_from_local_file(
                tmp_archive, rootfs_fd, dist_arch, context,
            )
        else:
            metadata = pull_image(
                image_ref, rootfs_fd, dist_arch, insecure=allow_insecure
            )

        # Write manifest.json when metadata is available (Docker pull
        # or OCI local). Skipped for plain tarballs (metadata is None).
        if metadata is not None:
            manifest_data = {
                "image_ref": (
                    metadata.get("image_ref") or
                    (image_ref if local_path is None else "")
                ),
                "arch": metadata.get("arch") or dist_arch,
                "manifest": metadata.get("manifest", {}),
                "image_config": metadata.get("image_config", {}),
            }
            try:
                with atomic_write(
                    container_manifest(install_name), "w",
                ) as fh:
                    json.dump(manifest_data, fh, indent=2)
            except OSError as exc:
                context.error(
                    f"Warning: could not write manifest.json: {exc}",
                    phase="install", resource=install_name,
                )

        # The fixups are the last thing the install writes into the
        # rootfs, and every one of them writes: a name resolved a second
        # time is a name that can have been re-pointed since the
        # extraction finished. `containers/<name>` is guest-writable on
        # Termux -- it sits under the bound $TERMUX_PREFIX -- so
        # os.path.isdir() on the composed path followed whatever a
        # concurrent same-user process left under it, and the three
        # writers then resolved it again, replacing etc/resolv.conf and
        # etc/hosts and chmod'ing and appending to passwd-like files in a
        # host directory of that process's choosing. `etc` is opened once
        # off the descriptor the extraction itself wrote through, and the
        # three fixups work from that one descriptor.
        etc_fd = open_etc(rootfs_fd)
        if etc_fd is not None:
            try:
                context.info(
                    "Updating '/etc/resolv.conf'...",
                    phase="install", resource=install_name,
                )
                write_resolv_conf_at(etc_fd)

                context.info(
                    "Updating '/etc/hosts'...",
                    phase="install", resource=install_name,
                )
                write_hosts_at(etc_fd)

                # os.path.isfile() answered for the *target* of a link an
                # image shipped under the name, while the append that
                # follows refuses one (O_NOFOLLOW) -- so the guard said
                # yes to exactly the entries the work then declined. The
                # lstat describes the entry itself, which is what decides
                # whether register_android_ids_at has a passwd to append
                # to at all.
                if _is_regular_at(etc_fd, "passwd"):
                    context.info(
                        "Registering Android-specific UIDs and GIDs...",
                        phase="install", resource=install_name,
                    )
                    register_android_ids_at(etc_fd)
            finally:
                os.close(etc_fd)

        setup_fake_sysdata(rootfs_dir, container_fd=container_fd)

    except KeyboardInterrupt:
        clear_bar()
        context.error(
            "Aborted by user.", phase="install", resource=install_name
        )
        _cleanup()
        raise
    except (EOFError, OSError, tarfile.TarError, RuntimeError) as exc:
        clear_bar()
        context.error(
            f"Failed to install: {exc}",
            phase="install", resource=install_name,
        )
        context.error(
            "See 'proot-distro install --help' on how to install "
            "distribution image.",
            phase="install", resource=install_name,
        )
        _cleanup()
        if isinstance(exc, RuntimeError):
            raise
        if isinstance(exc, (EOFError, tarfile.TarError)):
            raise IntegrityFailure(str(exc)) from exc
        failure = OperationFailure(str(exc))
        failure.reported = True
        raise failure from exc
    except Exception:
        _cleanup()
        raise
    finally:
        os.close(rootfs_fd)
        os.close(container_fd)
        if tmp_archive is not None:
            try:
                os.remove(tmp_archive)
            except OSError:
                pass

    context.info(
        "Finished installation.", phase="install", resource=install_name
    )
    entrypoint = (
        (metadata.get("image_config") or {}).get("config", {}).get("Entrypoint")
        if metadata else None
    )
    return ContainerInstallation(
        install_name, image_ref, dist_arch, bool(entrypoint)
    )


__all__ = (
    "_derive_local_name",
    "_is_local_path",
    "_is_url",
    "_resolve_install_name",
    "install_container",
)
