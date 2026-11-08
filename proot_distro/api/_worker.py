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

"""Short-lived executor for API operations backed by CLI-era handlers.

The command handlers still own the mature mutation pipelines, including
their cleanup, descriptor pinning, locks, and interruption rules. They also
use process-oriented control flow (terminal output, signal handlers, and
``SystemExit``), which must not leak into an embedding application. State
paths are also bound into modules when their interpreter imports them. This
module runs one requested stateful operation in a fresh process, applies that
instance's paths before import, streams bounded event frames through a private
descriptor, and reports a small JSON result to its parent. It is an
implementation detail, not a daemon or client-server protocol.
"""

import json
import os
import socket
import sys
import urllib.parse
from dataclasses import asdict
from types import SimpleNamespace

from proot_distro.message import capture_log_events, quote_error, set_quiet
from proot_distro.progress import capture_progress_events


_MAX_INPUT_BYTES = 1024 * 1024
_MAX_EVENT_BYTES = 64 * 1024


class _WorkerFailure(Exception):
    """A structured operation failure returned to the API parent."""

    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind


class _CredentialChannel:
    """Synchronous registry credential requests over a private socket."""

    def __init__(self, fd: int) -> None:
        os.set_inheritable(fd, False)
        self._socket = socket.socket(fileno=fd)
        self._buffer = bytearray()
        self._cache = {}

    def close(self) -> None:
        self._socket.close()

    def _readline(self) -> bytes:
        while b"\n" not in self._buffer:
            chunk = self._socket.recv(65536)
            if not chunk:
                raise RuntimeError("credential provider channel closed")
            self._buffer.extend(chunk)
            if len(self._buffer) > 1024 * 1024:
                raise RuntimeError("credential provider response is too large")
        line, _, remainder = self._buffer.partition(b"\n")
        self._buffer[:] = remainder
        return bytes(line)

    def __call__(self, registry: str, repository: str, actions: str):
        key = (registry, repository, actions)
        if key in self._cache:
            return self._cache[key]
        request = json.dumps({
            "registry": registry,
            "repository": repository,
            "actions": actions,
        }, ensure_ascii=True, separators=(",", ":")).encode("ascii") + b"\n"
        self._socket.sendall(request)
        response = json.loads(self._readline())
        if not isinstance(response, dict) or response.get("error"):
            raise RuntimeError("credential provider failed")
        credentials = response.get("credentials")
        if credentials is None:
            result = None
        elif isinstance(credentials, dict):
            username = credentials.get("username")
            password = credentials.get("password")
            if not isinstance(username, str) or not isinstance(password, str):
                raise RuntimeError("credential provider response is invalid")
            result = (username, password)
        else:
            raise RuntimeError("credential provider response is invalid")
        self._cache[key] = result
        return result


def _credential_channel_from_env():
    raw = os.environ.pop("_PROOT_DISTRO_CREDENTIAL_FD", "")
    if not raw:
        return None
    if not raw.isascii() or not raw.isdigit():
        raise RuntimeError("credential provider descriptor is invalid")
    return _CredentialChannel(int(raw))


def _open_stream(options: dict, name: str, mode: str):
    """Open one inherited API stream descriptor and prevent exec leakage."""
    streams = options.get("_stream_fds")
    if not isinstance(streams, dict):
        return None
    fd = streams.get(name)
    if isinstance(fd, bool) or not isinstance(fd, int) or fd <= 2:
        return None
    os.set_inheritable(fd, False)
    return os.fdopen(fd, mode)


def _channel_fd_from_env(name: str):
    """Take an inherited API channel descriptor from the environment."""
    raw = os.environ.pop(name, "")
    if not raw:
        return None
    if not raw.isascii() or not raw.isdigit():
        raise ValueError(f"{name} is not a valid descriptor")
    fd = int(raw)
    if fd <= 2:
        raise ValueError(f"{name} is not a valid descriptor")
    os.set_inheritable(fd, False)
    return fd


def _write_event(fd: int, event: dict) -> None:
    """Write one bounded newline-delimited event frame."""
    data = json.dumps(
        event, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii") + b"\n"
    if len(data) > _MAX_EVENT_BYTES:
        raise RuntimeError("operation event is too large")
    while data:
        written = os.write(fd, data)
        data = data[written:]


def _safe_source_resource(source):
    """Return a display-safe source without URL credentials or queries."""
    if not isinstance(source, str) or not source:
        return None
    try:
        parsed = urllib.parse.urlsplit(source)
    except ValueError:
        return None
    if not parsed.scheme or not parsed.netloc:
        return source
    authority = parsed.netloc.rsplit("@", 1)[-1]
    return urllib.parse.urlunsplit((
        parsed.scheme, authority, parsed.path, "", "",
    ))


def _default_event_resource(operation: str, options: dict):
    """Identify the operation's primary subject for otherwise bare events."""
    if operation == "install":
        return options.get("name") or _safe_source_resource(
            options.get("source")
        )
    if operation in ("remove_container", "reset_container"):
        return options.get("name")
    if operation == "rename_container":
        return options.get("new_name")
    if operation in ("copy", "sync"):
        return options.get("destination")
    if operation == "backup":
        return options.get("name")
    if operation == "restore":
        return options.get("archive")
    if operation == "build":
        tags = options.get("tags")
        if isinstance(tags, list) and tags:
            return tags[0]
        return options.get("install_as") or options.get("path")
    if operation == "push":
        return options.get("image_ref")
    if operation == "remove_image":
        return options.get("identifier")
    if operation in ("start_detached", "inspect_session"):
        return options.get("container")
    if operation == "kill_session":
        return options.get("target")
    return None


def _enable_structured_locks() -> None:
    """Turn context-manager lock conflicts into structured failures."""
    from proot_distro.locking import _FlockBase

    def enter(lock):
        if not lock.acquire():
            detail = lock.blocked_detail()
            if detail:
                message = (
                    f"cannot lock {lock._label} '{lock._display}': {detail}"
                )
            else:
                message = (
                    f"{lock._label} '{lock._display}' is busy"
                    f"{lock.holder_hint()}."
                )
            raise _WorkerFailure("busy", message)
        return lock

    _FlockBase.__enter__ = enter


def _write_error(kind: str, message: str) -> None:
    _write_result({"_error": {"kind": kind, "message": message}})


def _apply_registry_credentials(options: dict) -> None:
    """Apply one validated credential payload to this one-shot worker."""
    credentials = options.get("credentials")
    if credentials is None:
        os.environ.pop("PD_DOCKER_AUTH", None)
        return
    if not isinstance(credentials, dict):
        raise ValueError("registry credentials are not valid")
    username = credentials.get("username")
    password = credentials.get("password")
    if not isinstance(username, str) or not isinstance(password, str):
        raise ValueError("registry credentials are not valid")
    os.environ["PD_DOCKER_AUTH"] = f"{username}:{password}"


def _list_containers(_options: dict) -> dict:
    from proot_distro.api.program import _container_inventory

    return {"items": [asdict(item) for item in _container_inventory()]}


def _list_images(_options: dict) -> dict:
    from proot_distro.api.program import _image_inventory

    return {"items": [asdict(item) for item in _image_inventory()]}


def _list_sessions(_options: dict) -> dict:
    from proot_distro.api.program import _session_inventory

    return {"items": [asdict(item) for item in _session_inventory()]}


def _install(options: dict) -> dict:
    from proot_distro.commands.install import (
        _derive_local_name,
        _is_local_path,
        _is_url,
        command_install,
    )
    from proot_distro.helpers.docker.refs import derive_alias

    source = options["source"]
    name = options.get("name")
    if name:
        installed_name = name
    elif _is_local_path(source):
        installed_name = _derive_local_name(os.path.expanduser(source))
    elif _is_url(source):
        url_path = source.split("?", 1)[0].split("#", 1)[0]
        installed_name = _derive_local_name(url_path)
    else:
        installed_name = derive_alias(source)
    if _is_local_path(source) and not os.path.isfile(os.path.expanduser(source)):
        raise _WorkerFailure(
            "not_found", f"local archive '{source}' does not exist"
        )
    from proot_distro.paths import container_entry_lstat
    if container_entry_lstat(installed_name) is not None:
        raise _WorkerFailure(
            "already_exists", f"container '{installed_name}' already exists"
        )
    _apply_registry_credentials(options)
    command_install(SimpleNamespace(
        image_ref=source,
        custom_container_name=name,
        override_arch=options.get("architecture"),
        allow_insecure=options.get("allow_insecure", False),
        quiet=True,
        _api_raise_errors=True,
    ))

    return {"container_name": installed_name}


def _remove(options: dict) -> dict:
    from proot_distro.operations import remove_container

    name = options["name"]
    result = remove_container(name)
    return {"container_name": result.name}


def _rename(options: dict) -> dict:
    from proot_distro.operations import rename_container

    old_name = options["old_name"]
    new_name = options["new_name"]
    result = rename_container(old_name, new_name)
    return {"container_name": result.new_name}


def _reset(options: dict) -> dict:
    from proot_distro.commands.reset import command_reset
    from proot_distro.paths import container_image_origin, container_is_installed

    name = options["name"]
    if not container_is_installed(name):
        raise _WorkerFailure(
            "not_found", f"container '{name}' is not installed"
        )
    image_ref, _arch = container_image_origin(name)
    if not image_ref:
        raise _WorkerFailure(
            "unsupported",
            f"container '{name}' has no OCI manifest and cannot be reset",
        )
    _apply_registry_credentials(options)
    command_reset(SimpleNamespace(
        container_name=name, quiet=True, _api_raise_errors=True,
    ))
    return {"container_name": name}


def _copy(options: dict) -> dict:
    from proot_distro.commands.copy import command_copy

    command_copy(SimpleNamespace(
        source=options["source"],
        destination=options["destination"],
        verbose=False,
        quiet=True,
        move=options.get("move", False),
        recursive=options.get("recursive", False),
        _api_raise_errors=True,
    ))
    return {
        "source": options["source"],
        "destination": options["destination"],
    }


def _sync(options: dict) -> dict:
    from proot_distro.commands.sync import command_sync

    command_sync(SimpleNamespace(
        source=options["source"],
        destination=options["destination"],
        verbose=False,
        quiet=True,
        checksum=options.get("checksum", False),
        delete=options.get("delete", False),
        _api_raise_errors=True,
    ))
    return {
        "source": options["source"],
        "destination": options["destination"],
    }


def _backup(options: dict) -> dict:
    from proot_distro.commands.backup import (
        _compression_mode,
        command_backup,
    )

    output = options["output"]
    output_stream = _open_stream(options, "backup_output", "wb")
    compression = options.get("compression")
    if output is None:
        if output_stream is None:
            raise ValueError("backup output is not valid")
        inferred_compression = ""
        if compression is None:
            compression = "none"
    else:
        try:
            inferred_compression = _compression_mode(output)
        except ValueError as exc:
            raise _WorkerFailure("unsupported", str(exc)) from exc
    if compression == "zstd":
        from proot_distro.compress import ZSTD_AVAILABLE, unavailable_msg
        if not ZSTD_AVAILABLE:
            raise _WorkerFailure(
                "unsupported", unavailable_msg("compression type 'zstd'")
            )
    from proot_distro.paths import container_is_installed
    if not container_is_installed(options["name"]):
        raise _WorkerFailure(
            "not_found", f"container '{options['name']}' does not exist"
        )
    if output is not None and os.path.lexists(output):
        raise _WorkerFailure(
            "already_exists", f"backup output '{output}' already exists"
        )
    try:
        command_backup(SimpleNamespace(
            container_name=options["name"],
            output=output,
            compression=compression,
            verbose=False,
            quiet=True,
            _api_output_file=output_stream,
        ))
    finally:
        if output_stream is not None:
            output_stream.close()
    if compression is None:
        mode = inferred_compression
        compression = {
            "": "none", "gz": "gzip", "bz2": "bzip2",
            "xz": "xz", "zst": "zstd",
        }[mode]
    return {
        "container_name": options["name"],
        "output": output,
        "compression": compression,
        "size_bytes": os.path.getsize(output) if output is not None else None,
    }


def _restore(options: dict) -> dict:
    from proot_distro.commands.restore import command_restore

    archive = options["archive"]
    archive_stream = _open_stream(options, "restore_input", "rb")
    if archive is None and archive_stream is None:
        raise ValueError("restore input is not valid")
    if archive is not None and not os.path.isfile(archive):
        raise _WorkerFailure(
            "not_found", f"backup archive '{archive}' does not exist"
        )
    try:
        name = command_restore(SimpleNamespace(
            archive=archive, verbose=False, quiet=True, _api_raise_errors=True,
            _api_archive_file=archive_stream,
        ))
    finally:
        if archive_stream is not None:
            archive_stream.close()
    if not isinstance(name, str):
        raise RuntimeError("restore did not identify a container")
    return {"container_name": name, "archive": archive}


def _build(options: dict) -> dict:
    from proot_distro.arch import get_device_cpu_arch
    from proot_distro.api.program import _image_inventory
    from proot_distro.commands.build import (
        _derive_tag_from_path,
        command_build,
    )
    from proot_distro.helpers.docker.refs import with_explicit_tag

    _apply_registry_credentials(options)
    build_output_fd = _channel_fd_from_env(
        "_PROOT_DISTRO_BUILD_OUTPUT_FD"
    )
    build_path = options["path"]
    dockerfile = options.get("dockerfile")
    dockerfile_stream = _open_stream(
        options, "dockerfile_input", "rb"
    )
    tags = list(options.get("tags") or [])
    if not os.path.isdir(build_path):
        raise _WorkerFailure(
            "not_found", f"build context '{build_path}' does not exist"
        )
    dockerfile_path = dockerfile or os.path.join(build_path, "Dockerfile")
    if dockerfile_stream is None and not os.path.isfile(dockerfile_path):
        raise _WorkerFailure(
            "not_found", f"Dockerfile '{dockerfile_path}' does not exist"
        )
    output_files = []
    for index, output in enumerate(options.get("outputs") or []):
        if output is not None and os.path.lexists(output):
            raise _WorkerFailure(
                "already_exists", f"build output '{output}' already exists"
            )
        if output is None:
            stream = _open_stream(options, f"build_output_{index}", "wb")
            if stream is None:
                raise ValueError("build output stream is not valid")
            output_files.append(stream)
    credential_channel = _credential_channel_from_env()
    args = SimpleNamespace(
        path=build_path,
        dockerfile=dockerfile,
        _api_dockerfile_file=dockerfile_stream,
        _api_close_dockerfile_file=True,
        tags=tags,
        build_args=list(options.get("build_args") or []),
        override_arch=options.get("architecture"),
        target_stage=options.get("target"),
        emulator=options.get("emulator"),
        outputs=[
            output for output in options.get("outputs") or []
            if output is not None
        ],
        _api_output_files=output_files,
        _api_build_output_fd=build_output_fd,
        install_as=options.get("install_as"),
        no_cache=options.get("no_cache", False),
        verbose=False,
        quiet=True,
    )
    try:
        if credential_channel is None:
            command_build(args)
        else:
            from proot_distro.helpers.docker.transport import (
                use_basic_auth_provider,
            )
            with use_basic_auth_provider(credential_channel):
                command_build(args)
    finally:
        if dockerfile_stream is not None and not dockerfile_stream.closed:
            dockerfile_stream.close()
        for output_file in output_files:
            output_file.close()
        if build_output_fd is not None:
            os.close(build_output_fd)
        if credential_channel is not None:
            credential_channel.close()

    if not tags:
        dockerfile_for_tag = (
            dockerfile if dockerfile is not None
            else os.path.join(build_path, "Dockerfile")
        )
        tags = [_derive_tag_from_path(build_path, dockerfile_for_tag)]
    tags = [with_explicit_tag(tag) for tag in tags]
    architecture = options.get("architecture") or get_device_cpu_arch()
    images = [
        asdict(image) for image in _image_inventory()
        if image.reference in tags and image.architecture == architecture
    ]
    return {
        "images": images,
        "outputs": list(options.get("outputs") or []),
        "installed_container": options.get("install_as"),
    }


def _push(options: dict) -> dict:
    from proot_distro.commands.push import command_push
    from proot_distro.api.program import _image_inventory
    from proot_distro.arch import get_device_cpu_arch

    _apply_registry_credentials(options)
    architecture = options.get("architecture") or get_device_cpu_arch()
    if not any(
        image.reference == options["image_ref"]
        and image.architecture == architecture
        for image in _image_inventory()
    ):
        raise _WorkerFailure(
            "not_found",
            f"image '{options['image_ref']}' ({architecture}) is not cached",
        )
    result = command_push(SimpleNamespace(
        image_ref=options["image_ref"],
        override_arch=options.get("architecture"),
        allow_insecure=options.get("allow_insecure", False),
        quiet=True,
        _api_raise_errors=True,
    ))
    if not isinstance(result, dict):
        raise RuntimeError("push did not return a result")
    return result


def _remove_image(options: dict) -> dict:
    from proot_distro.operations import remove_image

    result = remove_image(
        options["identifier"], architecture=options.get("architecture")
    )
    return {
        "removed": [{
            "identifier": image.identifier,
            "reference": image.reference or None,
            "repository": image.repository or None,
            "architecture": image.architecture or None,
            "image_id": image.image_id or None,
            "size_bytes": image.size_bytes,
            "layer_count": image.layer_count,
            "missing_layers": image.missing_layers,
            "created": image.created or None,
            "cached_at": image.cached_at,
        } for image in result.removed],
    }


def _clear_cache(options: dict) -> dict:
    from proot_distro.operations import clear_cache

    result = clear_cache(
        orphan=options.get("orphan", False),
        build_cache=options.get("build_cache", False),
    )
    return {
        "bytes_before": result.bytes_before,
        "bytes_after": result.bytes_after,
    }


def _kill_session(options: dict) -> dict:
    from proot_distro.commands.kill import _select_sessions, command_kill
    from proot_distro.session import active_sessions

    kill_all = options.get("all", False)
    target = options.get("target")
    selected = _select_sessions(active_sessions(), target, kill_all)
    pids = [session["pid"] for session in selected]
    command_kill(SimpleNamespace(
        target=target,
        all=kill_all,
        signal=options.get("signal"),
    ))
    return {"pids": pids}


def _start_detached(options: dict) -> dict:
    from proot_distro.api._session_worker import _args

    args = _args(options)
    args.detach = True
    stdout_fd = stderr_fd = None
    session_options = options.get("options") or {}
    stdout_path = session_options.get("detach_stdout")
    stderr_path = session_options.get("detach_stderr")
    flags = os.O_WRONLY | os.O_CREAT | os.O_APPEND
    flags |= getattr(os, "O_CLOEXEC", 0)
    try:
        if stdout_path is not None:
            stdout_fd = os.open(stdout_path, flags, 0o666)
        if stderr_path == "stdout":
            stderr_fd = stdout_fd
        elif stderr_path is not None:
            stderr_fd = os.open(stderr_path, flags, 0o666)
    except OSError:
        if stdout_fd is not None:
            os.close(stdout_fd)
        raise
    args._detach_stdout_fd = stdout_fd
    args._detach_stderr_fd = stderr_fd
    kind = options.get("kind")
    try:
        if kind == "login":
            from proot_distro.commands.login import command_login
            pid = command_login(args)
        elif kind == "run":
            from proot_distro.commands.run import command_run
            pid = command_run(args)
        else:
            raise ValueError("session kind is not valid")
    finally:
        for fd in {stdout_fd, stderr_fd} - {None}:
            os.close(fd)
    if isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0:
        raise RuntimeError("detached session did not return a valid PID")
    return {"pid": pid, "container": options["container"], "kind": kind}


def _inspect_session(options: dict) -> dict:
    from proot_distro.api._session_worker import _args

    args = _args(options)
    args._api_inspect = True
    kind = options.get("kind")
    if kind == "login":
        from proot_distro.commands.login import command_login
        result = command_login(args)
    elif kind == "run":
        from proot_distro.commands.run import command_run
        result = command_run(args)
    else:
        raise ValueError("session kind is not valid")
    if not isinstance(result, dict):
        raise RuntimeError("session inspection did not return a description")
    return {**result, "container": options["container"], "kind": kind}


_OPERATIONS = {
    "list_containers": _list_containers,
    "list_images": _list_images,
    "list_sessions": _list_sessions,
    "install": _install,
    "remove_container": _remove,
    "rename_container": _rename,
    "reset_container": _reset,
    "copy": _copy,
    "move": _copy,
    "sync": _sync,
    "backup": _backup,
    "restore": _restore,
    "build": _build,
    "push": _push,
    "remove_image": _remove_image,
    "clear_cache": _clear_cache,
    "kill_session": _kill_session,
    "start_detached": _start_detached,
    "run_detached": _start_detached,
    "inspect_start": _inspect_session,
    "inspect_run": _inspect_session,
}


def _read_request() -> tuple:
    raw = sys.stdin.buffer.read(_MAX_INPUT_BYTES + 1)
    if len(raw) > _MAX_INPUT_BYTES:
        raise ValueError("operation request is too large")
    request = json.loads(raw)
    if not isinstance(request, dict):
        raise ValueError("operation request must be an object")
    operation = request.get("operation")
    options = request.get("options")
    if operation not in _OPERATIONS or not isinstance(options, dict):
        raise ValueError("operation request is not valid")
    return operation, options


def _write_result(result: dict) -> None:
    data = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
    sys.stdout.write(data + "\n")
    sys.stdout.flush()


def main() -> int:
    """Execute one operation read from stdin; return a process exit code."""
    os.umask(0o022)
    set_quiet(True)
    events = []
    errors = []
    operation = ""
    event_resource = None
    event_fd = _channel_fd_from_env("_PROOT_DISTRO_EVENT_FD")

    def record(kind, text, phase, resource):
        event = {
            "kind": "warning" if kind == "error" else kind,
            "message": text,
            "phase": phase,
            "resource": resource or event_resource,
        }
        if event_fd is None:
            events.append(event)
        else:
            _write_event(event_fd, event)
        if kind == "error":
            errors.append(text)

    def record_progress(
        message, current, total, unit, phase, resource,
    ):
        event = {
            "kind": "progress",
            "message": message,
            "current": current,
            "total": total,
            "unit": unit,
            "phase": phase,
            "resource": resource or event_resource,
        }
        if event_fd is None:
            # A directly-invoked worker has no live event consumer. Coalesce
            # adjacent updates so its fallback result remains bounded.
            if (events and events[-1].get("kind") == "progress"
                    and events[-1].get("message") == message
                    and events[-1].get("unit") == unit
                    and events[-1].get("phase") == phase
                    and events[-1].get("resource") == event["resource"]):
                events[-1] = event
            else:
                events.append(event)
        else:
            _write_event(event_fd, event)

    try:
        operation, options = _read_request()
        event_resource = _default_event_resource(operation, options)
        _enable_structured_locks()
        with capture_log_events(record):
            with capture_progress_events(record_progress):
                result = _OPERATIONS[operation](options)
    except _WorkerFailure as exc:
        _write_error(exc.kind, str(exc))
        return 1
    except SystemExit as exc:
        for message in errors:
            sys.stderr.write(f"Error: {message}\n")
        sys.stderr.flush()
        message = errors[-1] if errors else f"operation exited: {exc.code}"
        _write_error("operation", message)
        code = exc.code if isinstance(exc.code, int) else 1
        return code if code != 0 else 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        from proot_distro.helpers.docker.transport import (
            RegistryAuthenticationError,
        )
        from proot_distro.operation_errors import (
            AlreadyExistsFailure,
            BusyFailure,
            IntegrityFailure,
            NetworkFailure,
            NotFoundFailure,
            UnsupportedFailure,
        )
        if isinstance(exc, RegistryAuthenticationError):
            _write_error("authentication", quote_error(exc))
            return 1
        if isinstance(exc, AlreadyExistsFailure):
            _write_error("already_exists", quote_error(exc))
            return 1
        for failure_type, kind in (
            (BusyFailure, "busy"),
            (IntegrityFailure, "integrity"),
            (NetworkFailure, "network"),
            (NotFoundFailure, "not_found"),
            (UnsupportedFailure, "unsupported"),
        ):
            if isinstance(exc, failure_type):
                _write_error(kind, quote_error(exc))
                return 1
        from proot_distro.commands.copy import CopyPartialError
        from proot_distro.commands.sync import SyncPartialError
        if isinstance(exc, (CopyPartialError, SyncPartialError)):
            _write_error("partial", quote_error(exc))
            return 1
        import tarfile
        if isinstance(exc, tarfile.TarError):
            _write_error("integrity", quote_error(exc))
            return 1
        if operation == "push":
            from proot_distro.helpers.download import NETWORK_ERRORS
            if isinstance(exc, NETWORK_ERRORS):
                _write_error("network", quote_error(exc))
                return 1
        _write_error("operation", f"operation failed: {quote_error(exc)}")
        return 1
    result["_events"] = []
    for event in events:
        if event["kind"] == "error":
            event = {**event, "kind": "warning"}
        if event["kind"] in ("info", "warning", "progress", "retry"):
            result["_events"].append(event)
    _write_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
