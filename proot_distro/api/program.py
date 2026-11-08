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

"""The public programmatic interface to PRoot-Distro.

``ProotDistro`` is a programmatic facade, not a network client. It reads
the same state as the command-line utility and delegates to the same
descriptor-safe inventory and registry implementations.
"""

import json
import os
import signal
import subprocess
import sys
from typing import Callable, Optional, Tuple

from proot_distro.arch import normalize_arch
from proot_distro.constants import (
    BASE_CACHE_DIR,
    IS_TERMUX,
    RUNTIME_DIR,
    TERMUX_APP_PACKAGE,
    TERMUX_HOME,
    TERMUX_PREFIX,
)
from proot_distro.helpers.docker.cache import iter_cached_images
from proot_distro.helpers.docker.search import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_LIMIT_MAX,
    search_images,
)
from proot_distro.helpers.download import NETWORK_ERRORS
from proot_distro.names import NAME_RULE_HINT, is_valid_name
from proot_distro.paths import installed_container_names, read_container_manifest
from proot_distro.session import active_sessions

from proot_distro.api.errors import (
    InvalidArgumentError,
    NetworkError,
    OperationError,
)
from proot_distro.api.events import EventKind, OperationEvent
from proot_distro.api.models import (
    ContainerInfo,
    ImageInfo,
    SearchHit,
    SearchResult,
    SessionInfo,
)


EventHandler = Callable[[OperationEvent], None]

_MAX_WORKER_ERROR_BYTES = 64 * 1024


def _text(value) -> Optional[str]:
    """Return a non-empty string as-is, otherwise None."""
    return value if isinstance(value, str) and value else None


def _container_info(name: str) -> ContainerInfo:
    """Build public metadata for one already-validated container name."""
    try:
        data = read_container_manifest(name)
    except (OSError, ValueError):
        data = {}

    image_ref = _text(data.get("image_ref"))
    architecture = _text(data.get("arch"))
    manifest = data.get("manifest")
    if not isinstance(manifest, dict):
        manifest = {}
    config = manifest.get("config")
    if not isinstance(config, dict):
        config = {}
    image_id = _text(config.get("digest"))

    return ContainerInfo(
        name=name,
        image_reference=image_ref,
        architecture=architecture,
        image_id=image_id,
    )


def _require_name(name, *, operation: str, argument: str = "name") -> str:
    """Validate and return a public container-name argument."""
    if not isinstance(name, str) or not is_valid_name(name):
        shown = name if isinstance(name, str) else repr(name)
        raise InvalidArgumentError(
            f"{argument} '{shown}' is not valid. {NAME_RULE_HINT}",
            operation=operation,
        )
    return name


def _worker_environment() -> dict:
    """Environment that recreates this process's default state roots."""
    env = os.environ.copy()
    env["PD_FORCE_NO_COLORS"] = "1"
    # The application may have imported this package from a path it added
    # to sys.path at runtime. Child interpreters do not inherit sys.path, so
    # explicitly put this package's parent first and guarantee that the
    # worker executes the same code as the facade that launched it.
    package_parent = os.path.dirname(os.path.dirname(os.path.dirname(
        os.path.abspath(__file__)
    )))
    inherited_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        package_parent +
        (os.pathsep + inherited_pythonpath if inherited_pythonpath else "")
    )
    if IS_TERMUX:
        env["TERMUX__PREFIX"] = TERMUX_PREFIX
        env["TERMUX__HOME"] = TERMUX_HOME
        env["TERMUX_APP__PACKAGE_NAME"] = TERMUX_APP_PACKAGE
        if not (
            env.get("TERMUX_VERSION") or
            env.get("TERMUX_APP__APP_VERSION_NAME")
        ):
            env["TERMUX_VERSION"] = "unknown"
    else:
        env["XDG_DATA_HOME"] = os.path.dirname(RUNTIME_DIR)
        env["XDG_CACHE_HOME"] = os.path.dirname(BASE_CACHE_DIR)
        for name in (
            "TERMUX_VERSION",
            "TERMUX_APP__APP_VERSION_NAME",
            "TERMUX_APP__PACKAGE_NAME",
            "TERMUX__HOME",
            "TERMUX__PREFIX",
        ):
            env.pop(name, None)
    return env


def _worker_error(operation: str, stderr: bytes) -> OperationError:
    """Translate bounded worker diagnostics into one public exception."""
    tail = stderr[-_MAX_WORKER_ERROR_BYTES:]
    detail = tail.decode("utf-8", "backslashreplace").strip()
    if "Traceback (most recent call last):" in detail:
        lines = [line for line in detail.splitlines() if line.strip()]
        reason = lines[-1] if lines else "unexpected worker failure"
        detail = f"{operation} worker failed: {reason}"
    if not detail:
        detail = f"{operation} failed."
    return OperationError(detail, operation=operation)


def _run_worker(operation: str, options: dict) -> dict:
    """Run one mutation in an isolated child and return its JSON result."""
    request = json.dumps(
        {"operation": operation, "options": options},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    try:
        proc = subprocess.Popen(
            [sys.executable, "-m", "proot_distro.api._worker"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=_worker_environment(),
            start_new_session=True,
        )
    except OSError as exc:
        raise OperationError(
            f"cannot start the {operation} worker: {exc}", operation=operation,
        ) from exc

    try:
        stdout, stderr = proc.communicate(request)
    except KeyboardInterrupt:
        # Give the command handler the same interrupt the CLI would receive.
        # Rename defers it while rewriting l2s links, so communicate waits
        # until that consistency-critical section is complete.
        try:
            os.killpg(proc.pid, signal.SIGINT)
        except OSError:
            pass
        proc.communicate()
        raise
    except BaseException:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        proc.wait()
        raise

    if proc.returncode != 0:
        raise _worker_error(operation, stderr)
    try:
        result = json.loads(stdout)
    except (ValueError, UnicodeDecodeError) as exc:
        raise OperationError(
            f"{operation} worker returned an invalid result.",
            operation=operation,
        ) from exc
    if not isinstance(result, dict):
        raise OperationError(
            f"{operation} worker returned an invalid result.",
            operation=operation,
        )
    return result


class ProotDistro:
    """Standardized programmatic access to PRoot-Distro operations.

    The facade does not connect to a service and starts no persistent
    background process. Methods execute in the caller's process unless an
    operation's documented process semantics require a short-lived child.

    ``event_handler`` is optional. When provided, it is called synchronously
    for informational events such as network retries. Exceptions raised by
    the handler propagate to the caller.
    """

    def __init__(self, *, event_handler: Optional[EventHandler] = None) -> None:
        if event_handler is not None and not callable(event_handler):
            raise InvalidArgumentError(
                "event_handler must be callable or None.", operation="initialize"
            )
        self._event_handler = event_handler

    def _event(self, operation: str, kind: EventKind, message: str) -> None:
        if self._event_handler is not None:
            self._event_handler(OperationEvent(operation, kind, message))

    def list_containers(self) -> Tuple[ContainerInfo, ...]:
        """Return installed containers sorted by name."""
        return tuple(_container_info(name) for name in installed_container_names())

    def list_images(self) -> Tuple[ImageInfo, ...]:
        """Return locally cached OCI images in deterministic order."""
        images = []
        for record in iter_cached_images():
            reference = _text(record["image_ref"])
            image_hex = _text(record["image_id"])
            identifier = reference or (image_hex[:12] if image_hex else record["key"])
            images.append(ImageInfo(
                identifier=identifier,
                reference=reference,
                repository=_text(record["repo"]),
                architecture=_text(record["arch"]),
                image_id=_text(record.get("config_digest")),
                size_bytes=record["size"],
                layer_count=len(record["layers"]),
                missing_layers=record["missing"],
                created=_text(record["created"]),
                cached_at=float(record["cached_at"]),
            ))
        return tuple(images)

    def list_sessions(self) -> Tuple[SessionInfo, ...]:
        """Return validated records for every live PRoot-Distro session."""
        return tuple(
            SessionInfo(
                pid=record["pid"],
                container=record["container"],
                kind=record["kind"],
                command=tuple(record["command"]),
                user=record["user"],
                start_time=record["start_time"],
                isolated=record["isolated"],
                minimal=record["minimal"],
                detached=record["detach"],
            )
            for record in active_sessions()
        )

    def install(
        self,
        source,
        *,
        name: Optional[str] = None,
        architecture: Optional[str] = None,
        allow_insecure: bool = False,
    ) -> ContainerInfo:
        """Install a container from an image reference, URL, or archive."""
        source_is_path = isinstance(source, os.PathLike)
        try:
            source = os.fspath(source)
        except TypeError:
            raise InvalidArgumentError(
                "source must be a string or path-like object.",
                operation="install",
            ) from None
        if not isinstance(source, str) or not source:
            raise InvalidArgumentError(
                "source must be a non-empty text path or image reference.",
                operation="install",
            )
        if source_is_path:
            # A PathLike object is an unambiguous request for a local path.
            # Make a relative one explicit before handing it to the command
            # pipeline, whose string syntax deliberately treats bare tokens
            # as image references for CLI compatibility.
            source = os.path.abspath(os.path.expanduser(source))
        if name is not None:
            _require_name(name, operation="install")
        if architecture is not None:
            if not isinstance(architecture, str):
                raise InvalidArgumentError(
                    "architecture must be a string or None.",
                    operation="install",
                )
            normalized_arch = normalize_arch(architecture)
            if normalized_arch is None:
                raise InvalidArgumentError(
                    f"unsupported architecture '{architecture}'.",
                    operation="install",
                )
            architecture = normalized_arch
        if not isinstance(allow_insecure, bool):
            raise InvalidArgumentError(
                "allow_insecure must be a boolean.", operation="install"
            )

        result = _run_worker("install", {
            "source": source,
            "name": name,
            "architecture": architecture,
            "allow_insecure": allow_insecure,
        })
        installed_name = result.get("container_name")
        if not isinstance(installed_name, str):
            raise OperationError(
                "install worker did not identify the installed container.",
                operation="install",
            )
        return _container_info(installed_name)

    def remove_container(self, name: str) -> ContainerInfo:
        """Permanently remove a container and return its former metadata."""
        name = _require_name(name, operation="remove_container")
        previous = _container_info(name)
        _run_worker("remove_container", {"name": name})
        return previous

    def rename_container(self, old_name: str, new_name: str) -> ContainerInfo:
        """Rename a container and return its metadata under the new name."""
        old_name = _require_name(
            old_name, operation="rename_container", argument="old_name"
        )
        new_name = _require_name(
            new_name, operation="rename_container", argument="new_name"
        )
        if old_name == new_name:
            raise InvalidArgumentError(
                "old_name and new_name must differ.",
                operation="rename_container",
            )
        _run_worker("rename_container", {
            "old_name": old_name,
            "new_name": new_name,
        })
        return _container_info(new_name)

    def reset_container(self, name: str) -> ContainerInfo:
        """Reinstall a container from its recorded OCI image."""
        name = _require_name(name, operation="reset_container")
        _run_worker("reset_container", {"name": name})
        return _container_info(name)

    def search(
        self, query: str, *, limit: int = SEARCH_DEFAULT_LIMIT,
    ) -> SearchResult:
        """Search public Docker Hub repositories.

        Search is the only method in this initial API surface that performs
        network I/O. Registry credentials are never sent to Docker Hub's
        public search endpoint.
        """
        if not isinstance(query, str):
            raise InvalidArgumentError(
                "query must be a string.", operation="search"
            )
        normalized = query.strip()
        if not normalized:
            raise InvalidArgumentError(
                "query must not be empty.", operation="search"
            )
        try:
            normalized.encode("utf-8")
        except UnicodeError:
            raise InvalidArgumentError(
                "query must contain valid UTF-8 text.", operation="search"
            ) from None
        if isinstance(limit, bool) or not isinstance(limit, int):
            raise InvalidArgumentError(
                "limit must be a whole number.", operation="search"
            )
        if limit < 1 or limit > SEARCH_LIMIT_MAX:
            raise InvalidArgumentError(
                f"limit must be between 1 and {SEARCH_LIMIT_MAX}.",
                operation="search",
            )

        def on_retry(message: str) -> None:
            self._event("search", EventKind.RETRY, message)

        try:
            records, total = search_images(
                normalized,
                limit,
                on_retry=on_retry if self._event_handler is not None else None,
            )
        except NETWORK_ERRORS as exc:
            raise NetworkError(str(exc), operation="search") from exc
        except RuntimeError as exc:
            raise OperationError(str(exc), operation="search") from exc

        hits = tuple(SearchHit(
            name=record["name"],
            description=record["description"],
            stars=record["stars"],
            pulls=record["pulls"],
            official=record["official"],
            automated=record["automated"],
        ) for record in records)
        return SearchResult(query=normalized, total=total, hits=hits)


__all__ = ("EventHandler", "ProotDistro")
