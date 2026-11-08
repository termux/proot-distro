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


_OPERATIONS = {
    "list_containers": _list_containers,
    "list_images": _list_images,
    "list_sessions": _list_sessions,
    "install": _install,
    "remove_container": _remove,
    "rename_container": _rename,
    "reset_container": _reset,
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
