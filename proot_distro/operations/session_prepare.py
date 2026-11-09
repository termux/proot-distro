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

"""Descriptor-pinned preparation shared by login and run adapters.

Preparation owns validation, the shared container lock, secure state reads,
guest identity and environment resolution, and Proot argv assembly. It never
forks, registers a session, executes a process, prints a command, or exits.
``PreparedSession`` deliberately owns the live descriptors separately from the
frozen ``SessionLaunch`` metadata and releases them idempotently.
"""

import os
import shlex
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

from proot_distro import dirfd
from proot_distro.arch import (
    detect_installed_arch,
    get_device_cpu_arch,
    resolve_emulator_args,
    resolve_proot_bin,
)
from proot_distro.constants import (
    DEFAULT_FAKE_KERNEL_RELEASE,
    DEFAULT_PATH_ENV,
    IS_TERMUX,
    PROGRAM_NAME,
    TERMUX_HOME,
    TERMUX_PREFIX,
)
from proot_distro.guestfile import guest_file_exists
from proot_distro.locking import ContainerLock
from proot_distro.message import quote_error
from proot_distro.names import NAME_RULE_HINT, is_valid_name
from proot_distro.operations.context import OperationContext
from proot_distro.session_errors import (
    SessionNotFoundFailure,
    SessionValidationFailure,
)
from proot_distro.operations.locks import operation_lock
from proot_distro.operations.models import SessionLaunch
from proot_distro.paths import (
    container_rootfs,
    manifest_image_config,
    open_container_pair,
    read_container_manifest,
    read_container_manifest_at,
)
from proot_distro.session_command import build_proot_args
from proot_distro.session_env import (
    ANDROID_HOST_ENV_VARS,
    identity_env_pairs,
    image_env_pairs,
    inject_termux_profile,
)
from proot_distro.session_identity import (
    find_passwd_by_uid,
    passwd_available,
    passwd_field,
    read_group_gid,
    read_passwd_entry,
    shell_available,
)
from proot_distro.session_migrate import migrate_legacy_rootfs
from proot_distro.session_quoting import dq
from proot_distro.sysdata import setup_fake_sysdata


@dataclass(frozen=True)
class SessionOptions:
    """Normalized options used by both login and image-command sessions."""

    user: str = "root"
    environment: Tuple[str, ...] = ()
    binds: Tuple[str, ...] = ()
    work_dir: Optional[str] = None
    redirect_ports: bool = False
    shared_home: bool = False
    shared_tmp: bool = False
    shared_x11: bool = False
    isolated: bool = False
    minimal: bool = False
    no_link2symlink: bool = False
    no_sysvipc: bool = False
    no_kill_on_exit: bool = False
    emulator: Optional[str] = None
    kernel: Optional[str] = None
    hostname: Optional[str] = None


class PreparedSession:
    """A launch description paired with its lock and pinned descriptors."""

    def __init__(self, launch, lock, container_fd, rootfs_fd):
        self.launch = launch
        self._lock = lock
        self.container_fd = container_fd
        self.rootfs_fd = rootfs_fd
        self._closed = False

    def disown_lock(self) -> None:
        """Transfer lock ownership to a detached descendant."""
        self._lock.disown()

    def inherited_lock_fd(self):
        """Return the lock descriptor an exec-based launcher must preserve."""
        return self._lock.inheritable_fd()

    def close(self) -> None:
        """Close pinned descriptors; safe to call more than once."""
        if self._closed:
            return
        self._closed = True
        for fd in (self.rootfs_fd, self.container_fd):
            try:
                os.close(fd)
            except OSError:
                pass


def session_options(options=None) -> SessionOptions:
    """Validate a mapping or return an already normalized option record."""
    if options is None:
        return SessionOptions()
    if isinstance(options, SessionOptions):
        options = {
            "user": options.user,
            "env": options.environment,
            "bind": options.binds,
            "work_dir": options.work_dir,
            "redirect_ports": options.redirect_ports,
            "shared_home": options.shared_home,
            "shared_tmp": options.shared_tmp,
            "shared_x11": options.shared_x11,
            "isolated": options.isolated,
            "minimal": options.minimal,
            "no_link2symlink": options.no_link2symlink,
            "no_sysvipc": options.no_sysvipc,
            "no_kill_on_exit": options.no_kill_on_exit,
            "emulator": options.emulator,
            "kernel": options.kernel,
            "hostname": options.hostname,
        }
    if not isinstance(options, Mapping):
        raise SessionValidationFailure("session options must be a mapping.")

    user = options.get("user", "root") or "root"
    if not isinstance(user, str) or "\0" in user:
        raise SessionValidationFailure("session user must be a string.")

    environment = _string_tuple(
        options.get("env", options.get("environment", ())), "environment"
    )
    binds = _string_tuple(
        options.get("bind", options.get("binds", ())), "binds"
    )

    optional = {}
    for key in ("work_dir", "emulator", "kernel", "hostname"):
        value = options.get(key)
        if value is not None and (not isinstance(value, str) or "\0" in value):
            raise SessionValidationFailure(f"{key} must be a string or None.")
        optional[key] = value

    flags = {}
    for key in (
        "redirect_ports", "shared_home", "shared_tmp", "shared_x11",
        "isolated", "minimal", "no_link2symlink", "no_sysvipc",
        "no_kill_on_exit",
    ):
        value = options.get(key, False)
        if not isinstance(value, bool):
            raise SessionValidationFailure(
                "session feature flags must be booleans."
            )
        flags[key] = value
    if flags["isolated"] and flags["minimal"]:
        raise SessionValidationFailure(
            "isolated and minimal are mutually exclusive."
        )

    return SessionOptions(
        user=user,
        environment=environment,
        binds=binds,
        **optional,
        **flags,
    )


@contextmanager
def prepare_session(
    kind: str,
    container_name: str,
    arguments=(),
    *,
    options=None,
    inspect: bool = False,
    context=None,
):
    """Yield a descriptor-pinned prepared login or run session."""
    context = context if context is not None else OperationContext()
    options = session_options(options)
    arguments = _validate_request(kind, container_name, arguments, inspect)
    lock = ContainerLock(
        container_name,
        exclusive=False,
        command=kind,
        inheritable=not inspect,
    )
    with operation_lock(lock):
        migrate_legacy_rootfs(container_name)
        try:
            container_fd, rootfs_fd = open_container_pair(
                container_name, raise_errors=True
            )
        except FileNotFoundError:
            raise SessionNotFoundFailure(
                f"container '{container_name}' is not installed."
            ) from None
        prepared = None
        try:
            launch = _prepare_with_rootfs(
                kind,
                container_name,
                arguments,
                options,
                inspect,
                context,
                container_fd,
                rootfs_fd,
            )
            prepared = PreparedSession(
                launch, lock, container_fd, rootfs_fd
            )
            yield prepared
        finally:
            if prepared is not None:
                prepared.close()
            else:
                for fd in (rootfs_fd, container_fd):
                    try:
                        os.close(fd)
                    except OSError:
                        pass


def _validate_request(kind, container_name, arguments, inspect):
    if kind not in ("login", "run"):
        raise SessionValidationFailure("session kind is not valid.")
    if not isinstance(container_name, str) or not is_valid_name(container_name):
        raise SessionValidationFailure(
            f"container name '{container_name}' is not valid. {NAME_RULE_HINT}"
        )
    arguments = _string_tuple(arguments, "session arguments")
    if not isinstance(inspect, bool):
        raise SessionValidationFailure("inspect must be a boolean.")
    return arguments


def _string_tuple(value, label):
    if isinstance(value, (str, bytes)):
        raise SessionValidationFailure(f"{label} must be an iterable of strings.")
    try:
        result = tuple(value or ())
    except TypeError:
        raise SessionValidationFailure(
            f"{label} must be an iterable of strings."
        ) from None
    if not all(isinstance(item, str) and "\0" not in item for item in result):
        raise SessionValidationFailure(f"{label} must contain only strings.")
    return result


def _read_manifest(container_fd, container_name, required):
    try:
        return read_container_manifest_at(container_fd)
    except FileNotFoundError:
        if required:
            raise SessionValidationFailure(
                f"no image manifest found for container '{container_name}' "
                "which is required for command 'run'."
            ) from None
    except (OSError, ValueError) as exc:
        if required:
            raise SessionValidationFailure(
                f"cannot read manifest.json for '{container_name}': "
                f"{quote_error(exc)}"
            ) from exc
    return None


def _image_string_list(config, key, container_name):
    value = config.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(
        isinstance(item, str) for item in value
    ):
        raise SessionValidationFailure(
            f"the image manifest for '{container_name}' declares a {key} "
            "that is not a list of strings; the image is malformed."
        )
    return list(value)


def _resolve_run(config, arguments, container_name, work_dir):
    entrypoint = _image_string_list(config, "Entrypoint", container_name)
    command = _image_string_list(config, "Cmd", container_name)
    if arguments:
        inner = entrypoint + list(arguments)
    elif entrypoint or command:
        inner = entrypoint + command
    else:
        raise SessionValidationFailure(
            f"the image manifest for '{container_name}' defines neither "
            "Entrypoint nor Cmd, and no command was given after '--'."
        )
    if not inner:
        raise SessionValidationFailure(
            f"resolved command is empty for container '{container_name}'."
        )
    if work_dir:
        return inner, work_dir
    image_work_dir = config.get("WorkingDir")
    if image_work_dir is not None and not isinstance(image_work_dir, str):
        raise SessionValidationFailure(
            f"the image manifest for '{container_name}' declares a "
            "WorkingDir that is not a string; the image is malformed."
        )
    return inner, image_work_dir or "/"


def _detect_dist_type(rootfs, rootfs_fd=None):
    return "termux" if guest_file_exists(
        rootfs, f"{TERMUX_PREFIX}/bin/login", root_fd=rootfs_fd
    ) else "normal"


def _resolve_login_user(rootfs, container_name, user_arg, rootfs_fd=None):
    if ":" in user_arg:
        user_spec, group_spec = user_arg.split(":", 1)
        if not user_spec or not group_spec:
            raise SessionValidationFailure(
                "'--user' with ':' separator requires both user and group "
                "to be non-empty."
            )
    else:
        user_spec, group_spec = user_arg, None

    if passwd_available(rootfs, root_fd=rootfs_fd):
        if user_spec.isdigit():
            uid = user_spec
            home, shell, primary_gid = find_passwd_by_uid(
                rootfs, user_spec, root_fd=rootfs_fd
            )
            home, shell = home or "/", shell or "/bin/sh"
        else:
            entry = read_passwd_entry(rootfs, user_spec, root_fd=rootfs_fd)
            if not entry:
                raise SessionValidationFailure(
                    f"no user '{user_spec}' defined in /etc/passwd."
                )
            uid = passwd_field(entry, 2)
            primary_gid = passwd_field(entry, 3)
            home = passwd_field(entry, 5) or "/"
            shell = passwd_field(entry, 6) or "/bin/sh"
            if not uid:
                raise SessionValidationFailure(
                    f"failed to retrieve UID for user '{user_spec}'."
                )
        if group_spec is None:
            gid = primary_gid or uid
        elif group_spec.isdigit():
            gid = group_spec
        else:
            gid = read_group_gid(rootfs, group_spec, root_fd=rootfs_fd)
            if not gid:
                raise SessionValidationFailure(
                    f"no group '{group_spec}' defined in /etc/group."
                )
    else:
        if user_spec == "root":
            uid = "0"
        elif user_spec.isdigit():
            uid = user_spec
        else:
            raise SessionValidationFailure(
                f"container '{container_name}' has no /etc/passwd; '--user' "
                "only accepts a numeric UID in this case."
            )
        if group_spec is None:
            gid = uid
        elif group_spec.isdigit():
            gid = group_spec
        else:
            raise SessionValidationFailure(
                f"container '{container_name}' has no /etc/group; '--user' "
                "only accepts a numeric GID in group specification."
            )
        home, shell = "/", "/bin/sh"
    return user_spec, uid, gid, home, shell


def _base_environment(container_name, manifest, *, distro_type, user, home,
                      options):
    env = {}
    if not options.minimal:
        if distro_type == "termux":
            env.update({
                "HOME": TERMUX_HOME,
                "PATH": f"{TERMUX_PREFIX}/bin",
                "PREFIX": TERMUX_PREFIX,
                "TMPDIR": f"{TERMUX_PREFIX}/tmp",
            })
        else:
            env["PATH"] = DEFAULT_PATH_ENV
            if IS_TERMUX:
                env.update({
                    "MOZ_FAKE_NO_SANDBOX": "1",
                    "MOZ_SHM_NO_SEALS": "1",
                    "PULSE_SERVER": "127.0.0.1",
                })
    for key, value in image_env_pairs(container_name, manifest=manifest):
        env[key] = value
    for key, value in identity_env_pairs(container_name, manifest=manifest):
        env[key] = value
    if IS_TERMUX and not options.isolated and not options.minimal:
        for key in ANDROID_HOST_ENV_VARS:
            value = os.environ.get(key, "")
            if value:
                env[key] = value
    for entry in options.environment:
        key, _, value = entry.partition("=")
        if key:
            env[key] = value
    if distro_type == "normal" and not options.minimal:
        env["HOME"], env["USER"] = home, user
    host_term = os.environ.get("TERM", "")
    if distro_type == "normal":
        env["TERM"] = host_term or "xterm-256color"
    elif host_term:
        env["TERM"] = host_term
    host_colorterm = os.environ.get("COLORTERM", "")
    if host_colorterm:
        env["COLORTERM"] = host_colorterm
    return env


def _check_shell(rootfs, shell, container_name, config, rootfs_fd):
    if shell_available(rootfs, shell, root_fd=rootfs_fd):
        return
    has_command = bool(
        (config.get("Entrypoint") or []) or (config.get("Cmd") or [])
    )
    if has_command:
        raise SessionValidationFailure(
            f"shell '{shell}' is not available in container "
            f"'{container_name}'. The image defines an Entrypoint or Cmd; "
            f"use '{PROGRAM_NAME} run {container_name}' instead."
        )
    raise SessionValidationFailure(
        f"shell '{shell}' is not available in container '{container_name}' "
        "and the image has no Entrypoint or Cmd defined."
    )


def _format_command(environment, argv):
    parts = ["env", "-i"]
    parts.extend(f"{key}={dq(value)}" for key, value in environment.items())
    parts.extend(dq(argument) for argument in argv)
    return " \\\n  ".join(parts)


def _prepare_with_rootfs(kind, container_name, arguments, options, inspect,
                         context, container_fd, rootfs_fd):
    rootfs = container_rootfs(container_name)
    distro_type = _detect_dist_type(rootfs, rootfs_fd)
    manifest = _read_manifest(
        container_fd, container_name, required=(kind == "run")
    )
    config = manifest_image_config(manifest or {})

    work_dir = options.work_dir or ""
    login_user = options.user
    uid = gid = home = shell = None
    if distro_type == "termux":
        work_dir = work_dir or TERMUX_HOME
        if kind == "run":
            inner, work_dir = _resolve_run(
                config, arguments, container_name, work_dir
            )
        else:
            inner = [f"{TERMUX_PREFIX}/bin/login"]
            if arguments:
                inner += ["-c", shlex.join(arguments)]
    else:
        login_user, uid, gid, home, shell = _resolve_login_user(
            rootfs, container_name, login_user, rootfs_fd
        )
        if kind == "run":
            inner, work_dir = _resolve_run(
                config, arguments, container_name, work_dir
            )
        else:
            work_dir = work_dir or home
            _check_shell(rootfs, shell, container_name, config, rootfs_fd)
            inner = (
                [shell, "-c", shlex.join(arguments)]
                if arguments else [shell, "-l"]
            )
        if IS_TERMUX and not options.minimal:
            setup_fake_sysdata(rootfs, container_fd=container_fd)

    child_env = _base_environment(
        container_name,
        manifest,
        distro_type=distro_type,
        user=login_user,
        home=home,
        options=options,
    )
    if IS_TERMUX and not options.isolated and not options.minimal:
        termux_bin = f"{TERMUX_PREFIX}/bin"
        components = [
            item for item in child_env.get("PATH", "").split(":")
            if item and item != termux_bin
        ]
        components.append(termux_bin)
        child_env["PATH"] = ":".join(components)
    if distro_type == "normal":
        inject_termux_profile(
            rootfs,
            child_env,
            rootfs_fd=rootfs_fd,
            termux_path=IS_TERMUX and not options.isolated and not options.minimal,
        )

    target_arch = detect_installed_arch(rootfs, rootfs_fd=rootfs_fd)
    if target_arch == "unknown":
        target_arch = get_device_cpu_arch()
    device_arch = get_device_cpu_arch()
    emulator_args = resolve_emulator_args(
        target_arch, device_arch, options.emulator or ""
    )
    if distro_type == "termux" and emulator_args:
        raise SessionValidationFailure(
            f"cannot run Termux-type container '{container_name}' under "
            f"emulation. The container architecture is '{target_arch}' but "
            f"the host is '{device_arch}'. Termux-type containers are not "
            "emulatable because the host and the container share the same "
            f"Termux prefix path ({TERMUX_PREFIX})."
        )

    executable = resolve_proot_bin()
    argv = build_proot_args(
        proot_bin=executable,
        rootfs=rootfs,
        rootfs_arg=rootfs if inspect else os.curdir,
        container_fd=container_fd,
        rootfs_fd=rootfs_fd,
        login_wd=work_dir,
        login_uid=uid,
        login_gid=gid,
        login_home=home,
        emu_args=emulator_args,
        need_emu=bool(emulator_args),
        target_arch=target_arch,
        hostname=options.hostname or "localhost",
        kernel_release=options.kernel or DEFAULT_FAKE_KERNEL_RELEASE,
        dist_type=distro_type,
        minimal=options.minimal,
        isolated=options.isolated,
        no_link2symlink=options.no_link2symlink,
        no_sysvipc=options.no_sysvipc,
        no_kill_on_exit=options.no_kill_on_exit,
        use_shared_home=options.shared_home,
        shared_tmp=options.shared_tmp,
        shared_x11=options.shared_x11,
        custom_binds=options.binds,
        redirect_ports=options.redirect_ports,
        inner=inner,
        context=context,
        resource=container_name,
    )

    if not options.minimal:
        for key in ("PROOT_NO_SECCOMP", "PROOT_VERBOSE"):
            value = os.environ.get(key)
            if value:
                child_env[key] = value
    if IS_TERMUX and distro_type != "termux":
        l2s_dir = dirfd.makedirs_at(rootfs_fd, rootfs, (".l2s",))
        if l2s_dir is not None:
            child_env["PROOT_L2S_DIR"] = l2s_dir
        else:
            child_env.pop("PROOT_L2S_DIR", None)
            context.warning(
                "container's .l2s is not a plain directory; leaving "
                "PROOT_L2S_DIR unset for this session.",
                phase="session-setup",
                resource=container_name,
            )
    child_env.pop("LD_PRELOAD", None)

    return SessionLaunch(
        kind=kind,
        container=container_name,
        executable=executable,
        argv=tuple(argv),
        environment=tuple(child_env.items()),
        inner_command=tuple(inner),
        rootfs=rootfs,
        work_dir=work_dir,
        user=login_user,
        uid=uid,
        gid=gid,
        home=home,
        shell=shell,
        distro_type=distro_type,
        architecture=target_arch,
        isolated=options.isolated,
        minimal=options.minimal,
        shell_command=_format_command(child_env, argv),
    )


def _build_normal_env(container_name, login_user, login_home, extra_env,
                      minimal, isolated):
    """Compatibility helper for direct environment-builder tests."""
    try:
        manifest = read_container_manifest(container_name)
    except (OSError, ValueError):
        manifest = None
    return _base_environment(
        container_name,
        manifest,
        distro_type="normal",
        user=login_user,
        home=login_home,
        options=SessionOptions(
            environment=tuple(extra_env), minimal=minimal, isolated=isolated
        ),
    )


def _build_termux_env(container_name, extra_env, minimal, isolated):
    """Compatibility helper for direct environment-builder tests."""
    try:
        manifest = read_container_manifest(container_name)
    except (OSError, ValueError):
        manifest = None
    return _base_environment(
        container_name,
        manifest,
        distro_type="termux",
        user="root",
        home=None,
        options=SessionOptions(
            environment=tuple(extra_env), minimal=minimal, isolated=isolated
        ),
    )


__all__ = (
    "PreparedSession",
    "SessionOptions",
    "prepare_session",
    "session_options",
)
