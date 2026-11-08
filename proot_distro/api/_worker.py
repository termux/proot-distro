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
instance's paths before import, and reports a small JSON result to its parent.
It is an implementation detail, not a daemon or client-server protocol.
"""

import json
import os
import sys
from dataclasses import asdict
from types import SimpleNamespace

from proot_distro.message import capture_log_events, quote_error, set_quiet
from proot_distro.progress import capture_progress_events


_MAX_INPUT_BYTES = 1024 * 1024


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
    _apply_registry_credentials(options)
    command_install(SimpleNamespace(
        image_ref=source,
        custom_container_name=name,
        override_arch=options.get("architecture"),
        allow_insecure=options.get("allow_insecure", False),
        quiet=True,
        _api_raise_errors=True,
    ))

    if name:
        installed_name = name
    elif _is_local_path(source):
        installed_name = _derive_local_name(os.path.expanduser(source))
    elif _is_url(source):
        url_path = source.split("?", 1)[0].split("#", 1)[0]
        installed_name = _derive_local_name(url_path)
    else:
        installed_name = derive_alias(source)
    return {"container_name": installed_name}


def _remove(options: dict) -> dict:
    from proot_distro.commands.remove import command_remove

    name = options["name"]
    command_remove(SimpleNamespace(
        target=name,
        image=False,
        override_arch=None,
        verbose=False,
        quiet=True,
    ))
    return {"container_name": name}


def _rename(options: dict) -> dict:
    from proot_distro.commands.rename import command_rename

    old_name = options["old_name"]
    new_name = options["new_name"]
    command_rename(SimpleNamespace(
        orig_name=old_name,
        new_name=new_name,
        quiet=True,
    ))
    return {"container_name": new_name}


def _reset(options: dict) -> dict:
    from proot_distro.commands.reset import command_reset

    name = options["name"]
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
    compression = options.get("compression")
    command_backup(SimpleNamespace(
        container_name=options["name"],
        output=output,
        compression=compression,
        verbose=False,
        quiet=True,
    ))
    if compression is None:
        mode = _compression_mode(output)
        compression = {
            "": "none", "gz": "gzip", "bz2": "bzip2",
            "xz": "xz", "zst": "zstd",
        }[mode]
    return {
        "container_name": options["name"],
        "output": output,
        "compression": compression,
        "size_bytes": os.path.getsize(output),
    }


def _restore(options: dict) -> dict:
    from proot_distro.commands.restore import command_restore

    archive = options["archive"]
    name = command_restore(SimpleNamespace(
        archive=archive, verbose=False, quiet=True,
    ))
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
    build_path = options["path"]
    dockerfile = options.get("dockerfile")
    tags = list(options.get("tags") or [])
    command_build(SimpleNamespace(
        path=build_path,
        dockerfile=dockerfile,
        tags=tags,
        build_args=list(options.get("build_args") or []),
        override_arch=options.get("architecture"),
        target_stage=options.get("target"),
        emulator=options.get("emulator"),
        outputs=list(options.get("outputs") or []),
        install_as=options.get("install_as"),
        no_cache=options.get("no_cache", False),
        verbose=False,
        quiet=True,
    ))

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

    _apply_registry_credentials(options)
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
    from proot_distro.api.program import _image_inventory
    from proot_distro.commands.remove import command_remove

    before = _image_inventory()
    command_remove(SimpleNamespace(
        target=options["identifier"],
        image=True,
        override_arch=options.get("architecture"),
        verbose=False,
        quiet=True,
    ))
    after = set(_image_inventory())
    return {
        "removed": [asdict(image) for image in before if image not in after],
    }


def _cache_size() -> int:
    from proot_distro.commands.clear_cache import _open_cache_root, _tree_size

    try:
        root_fd = _open_cache_root()
    except FileNotFoundError:
        return 0
    try:
        return _tree_size(root_fd)
    finally:
        os.close(root_fd)


def _clear_cache(options: dict) -> dict:
    from proot_distro.commands.clear_cache import command_clear_cache

    before = _cache_size()
    command_clear_cache(SimpleNamespace(
        orphan=options.get("orphan", False),
        build_cache=options.get("build_cache", False),
        verbose=False,
        quiet=True,
    ))
    after = _cache_size()
    return {"bytes_before": before, "bytes_after": after}


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

    def record(kind, text):
        events.append({"kind": kind, "message": text})

    def record_progress(message, current, total, unit):
        event = {
            "kind": "progress",
            "message": message,
            "current": current,
            "total": total,
            "unit": unit,
        }
        # Progress producers can update once per chunk. Preserve ordering
        # across phases but coalesce adjacent updates of the same phase so a
        # large image cannot make the worker result grow with every chunk.
        if (events and events[-1].get("kind") == "progress"
                and events[-1].get("message") == message
                and events[-1].get("unit") == unit):
            events[-1] = event
        else:
            events.append(event)

    try:
        operation, options = _read_request()
        with capture_log_events(record):
            with capture_progress_events(record_progress):
                result = _OPERATIONS[operation](options)
    except SystemExit as exc:
        for event in events:
            if event["kind"] == "error":
                sys.stderr.write(f"Error: {event['message']}\n")
        sys.stderr.flush()
        code = exc.code if isinstance(exc.code, int) else 1
        return code if code != 0 else 1
    except KeyboardInterrupt:
        return 130
    except Exception as exc:
        from proot_distro.helpers.docker.transport import (
            RegistryAuthenticationError,
        )
        if isinstance(exc, RegistryAuthenticationError):
            sys.stderr.write(f"Error: {quote_error(exc)}\n")
            sys.stderr.flush()
            return 4
        sys.stderr.write(f"Error: operation failed: {quote_error(exc)}\n")
        sys.stderr.flush()
        return 1
    result["_events"] = []
    for event in events:
        if event["kind"] == "error":
            event = {**event, "kind": "warning"}
        if event["kind"] in ("info", "warning", "progress"):
            result["_events"].append(event)
    _write_result(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
