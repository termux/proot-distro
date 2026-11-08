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
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from proot_distro.arch import normalize_arch
from proot_distro.constants import (
    IS_TERMUX,
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
from proot_distro.helpers.docker.refs import InvalidReference, parse_reference
from proot_distro.helpers.download import NETWORK_ERRORS
from proot_distro.names import NAME_RULE_HINT, is_valid_name
from proot_distro.paths import installed_container_names, read_container_manifest
from proot_distro.session import active_sessions

from proot_distro.api.errors import (
    AuthenticationError,
    InvalidArgumentError,
    NetworkError,
    OperationError,
)
from proot_distro.api.config import RuntimeConfig
from proot_distro.api.credentials import (
    CredentialProvider,
    CredentialRequest,
    RegistryCredentials,
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


def _container_inventory() -> Tuple[ContainerInfo, ...]:
    """Read container records using the active process configuration."""
    return tuple(_container_info(name) for name in installed_container_names())


def _image_inventory() -> Tuple[ImageInfo, ...]:
    """Read image records using the active process configuration."""
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


def _session_inventory() -> Tuple[SessionInfo, ...]:
    """Read session records using the active process configuration."""
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


def _require_name(name, *, operation: str, argument: str = "name") -> str:
    """Validate and return a public container-name argument."""
    if not isinstance(name, str) or not is_valid_name(name):
        shown = name if isinstance(name, str) else repr(name)
        raise InvalidArgumentError(
            f"{argument} '{shown}' is not valid. {NAME_RULE_HINT}",
            operation=operation,
        )
    return name


def _worker_environment(config: RuntimeConfig) -> dict:
    """Environment that recreates this process's default state roots."""
    env = os.environ.copy()
    env["PD_FORCE_NO_COLORS"] = "1"
    # API credentials are explicit and operation-scoped. Do not inherit the
    # CLI's ambient registry password into an unrelated application call.
    env.pop("PD_DOCKER_AUTH", None)
    env["_PROOT_DISTRO_API_RUNTIME_DIR"] = config.runtime_dir
    env["_PROOT_DISTRO_API_CACHE_DIR"] = config.cache_dir
    if config.proot_path is None:
        env.pop("PD_PROOT_BIN", None)
    else:
        env["PD_PROOT_BIN"] = config.proot_path
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


def _run_worker(operation: str, options: dict,
                config: Optional[RuntimeConfig] = None,
                event_handler: Optional[EventHandler] = None) -> dict:
    """Run one stateful operation in a child and return its JSON result."""
    config = config or RuntimeConfig.default()
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
            env=_worker_environment(config),
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
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

    if proc.returncode == 4:
        detail = _worker_error(operation, stderr)
        raise AuthenticationError(str(detail), operation=operation)
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
    raw_events = result.pop("_events", [])
    if not isinstance(raw_events, list):
        raise OperationError(
            f"{operation} worker returned invalid events.",
            operation=operation,
        )
    events = []
    for raw_event in raw_events:
        try:
            kind = EventKind(raw_event["kind"])
            message = raw_event["message"]
            if not isinstance(message, str):
                raise TypeError
            current = raw_event.get("current")
            total = raw_event.get("total")
            unit = raw_event.get("unit")
            if current is not None and (
                isinstance(current, bool) or not isinstance(current, int)
            ):
                raise TypeError
            if total is not None and (
                isinstance(total, bool) or not isinstance(total, int)
            ):
                raise TypeError
            if unit is not None and not isinstance(unit, str):
                raise TypeError
        except (KeyError, TypeError, ValueError):
            raise OperationError(
                f"{operation} worker returned an invalid event.",
                operation=operation,
            ) from None
        events.append(OperationEvent(
            operation, kind, message, current, total, unit
        ))
    if event_handler is not None:
        for event in events:
            event_handler(event)
    return result


@dataclass(frozen=True)
class _OperationContext:
    """Dependencies shared by operations of one public facade instance."""

    config: RuntimeConfig
    event_handler: Optional[EventHandler]
    credentials: Optional[RegistryCredentials]
    credential_provider: Optional[CredentialProvider]

    def event(self, operation: str, kind: EventKind, message: str) -> None:
        if self.event_handler is not None:
            self.event_handler(OperationEvent(operation, kind, message))

    def worker(self, operation: str, options: dict) -> dict:
        return _run_worker(
            operation, options, self.config, self.event_handler
        )

    def credentials_for(
        self, registry: str, repository: str, operation: str,
    ) -> Optional[RegistryCredentials]:
        """Resolve explicit credentials for one registry operation."""
        if self.credentials is not None:
            return self.credentials
        if self.credential_provider is None:
            return None
        request = CredentialRequest(registry, repository, operation)
        credentials = self.credential_provider(request)
        if credentials is not None and not isinstance(
            credentials, RegistryCredentials
        ):
            raise InvalidArgumentError(
                "credential_provider must return RegistryCredentials or None.",
                operation=operation,
            )
        return credentials

    @staticmethod
    def credential_payload(
        credentials: Optional[RegistryCredentials],
    ) -> Optional[dict]:
        if credentials is None:
            return None
        return {
            "username": credentials.username,
            "password": credentials.password,
        }


class ProotDistro:
    """Standardized programmatic access to PRoot-Distro operations.

    The facade does not connect to a service and starts no persistent
    background process. Stateful methods use a short-lived child so each
    instance can apply its own runtime configuration without changing the
    embedding process's environment or imported module constants.

    ``event_handler`` is optional. When provided, it is called synchronously
    for informational events such as network retries. Exceptions raised by
    the handler propagate to the caller.
    """

    def __init__(
        self,
        config: Optional[RuntimeConfig] = None,
        *,
        event_handler: Optional[EventHandler] = None,
        credentials: Optional[RegistryCredentials] = None,
        credential_provider: Optional[CredentialProvider] = None,
    ) -> None:
        if config is None:
            config = RuntimeConfig.default()
        elif not isinstance(config, RuntimeConfig):
            raise InvalidArgumentError(
                "config must be a RuntimeConfig instance or None.",
                operation="initialize",
            )
        if event_handler is not None and not callable(event_handler):
            raise InvalidArgumentError(
                "event_handler must be callable or None.", operation="initialize"
            )
        if credentials is not None and not isinstance(
            credentials, RegistryCredentials
        ):
            raise InvalidArgumentError(
                "credentials must be RegistryCredentials or None.",
                operation="initialize",
            )
        if credential_provider is not None and not callable(
            credential_provider
        ):
            raise InvalidArgumentError(
                "credential_provider must be callable or None.",
                operation="initialize",
            )
        if credentials is not None and credential_provider is not None:
            raise InvalidArgumentError(
                "credentials and credential_provider are mutually exclusive.",
                operation="initialize",
            )
        self._context = _OperationContext(
            config, event_handler, credentials, credential_provider
        )

    @property
    def config(self) -> RuntimeConfig:
        """The immutable runtime configuration used by this instance."""
        return self._context.config

    def _event(self, operation: str, kind: EventKind, message: str) -> None:
        self._context.event(operation, kind, message)

    def list_containers(self) -> Tuple[ContainerInfo, ...]:
        """Return installed containers sorted by name."""
        result = self._context.worker("list_containers", {})
        items = result.get("items")
        if not isinstance(items, list):
            raise OperationError(
                "container inventory worker returned an invalid result.",
                operation="list_containers",
            )
        try:
            return tuple(ContainerInfo(**item) for item in items)
        except (TypeError, ValueError):
            raise OperationError(
                "container inventory worker returned an invalid result.",
                operation="list_containers",
            ) from None

    def list_images(self) -> Tuple[ImageInfo, ...]:
        """Return locally cached OCI images in deterministic order."""
        result = self._context.worker("list_images", {})
        items = result.get("items")
        if not isinstance(items, list):
            raise OperationError(
                "image inventory worker returned an invalid result.",
                operation="list_images",
            )
        try:
            return tuple(ImageInfo(**item) for item in items)
        except (TypeError, ValueError):
            raise OperationError(
                "image inventory worker returned an invalid result.",
                operation="list_images",
            ) from None

    def list_sessions(self) -> Tuple[SessionInfo, ...]:
        """Return validated records for every live PRoot-Distro session."""
        result = self._context.worker("list_sessions", {})
        items = result.get("items")
        if not isinstance(items, list):
            raise OperationError(
                "session inventory worker returned an invalid result.",
                operation="list_sessions",
            )
        try:
            return tuple(SessionInfo(
                pid=item["pid"],
                container=item["container"],
                kind=item["kind"],
                command=tuple(item["command"]),
                user=item["user"],
                start_time=item["start_time"],
                isolated=item["isolated"],
                minimal=item["minimal"],
                detached=item["detached"],
            ) for item in items)
        except (KeyError, TypeError, ValueError):
            raise OperationError(
                "session inventory worker returned an invalid result.",
                operation="list_sessions",
            ) from None

    def _installed_container(self, name: str, operation: str) -> ContainerInfo:
        """Return *name* from this instance's configured state."""
        for container in self.list_containers():
            if container.name == name:
                return container
        raise OperationError(
            f"{operation} completed but container '{name}' is not installed.",
            operation=operation,
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
        is_local = source.startswith(("/", "./", "../", "~"))
        is_url = source.startswith(("http://", "https://"))
        reference = None
        if not is_local and not is_url:
            try:
                reference = parse_reference(source)
            except InvalidReference as exc:
                raise InvalidArgumentError(
                    f"invalid image reference: {exc}", operation="install"
                ) from None
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

        registry_credentials = None
        if reference is not None:
            registry_credentials = self._context.credentials_for(
                reference.registry or "docker.io",
                reference.repo,
                "install",
            )

        result = self._context.worker("install", {
            "source": source,
            "name": name,
            "architecture": architecture,
            "allow_insecure": allow_insecure,
            "credentials": self._context.credential_payload(
                registry_credentials
            ),
        })
        installed_name = result.get("container_name")
        if not isinstance(installed_name, str):
            raise OperationError(
                "install worker did not identify the installed container.",
                operation="install",
            )
        return self._installed_container(installed_name, "install")

    def remove_container(self, name: str) -> ContainerInfo:
        """Permanently remove a container and return its former metadata."""
        name = _require_name(name, operation="remove_container")
        previous = None
        for container in self.list_containers():
            if container.name == name:
                previous = container
                break
        self._context.worker("remove_container", {"name": name})
        return previous or ContainerInfo(name, None, None, None)

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
        self._context.worker("rename_container", {
            "old_name": old_name,
            "new_name": new_name,
        })
        return self._installed_container(new_name, "rename_container")

    def reset_container(self, name: str) -> ContainerInfo:
        """Reinstall a container from its recorded OCI image."""
        name = _require_name(name, operation="reset_container")
        current = None
        for container in self.list_containers():
            if container.name == name:
                current = container
                break
        registry_credentials = None
        if current is not None and current.image_reference:
            try:
                reference = parse_reference(current.image_reference)
            except InvalidReference:
                reference = None
            if reference is not None:
                registry_credentials = self._context.credentials_for(
                    reference.registry or "docker.io",
                    reference.repo,
                    "reset_container",
                )
        self._context.worker("reset_container", {
            "name": name,
            "credentials": self._context.credential_payload(
                registry_credentials
            ),
        })
        return self._installed_container(name, "reset_container")

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
                on_retry=(on_retry if self._context.event_handler is not None
                          else None),
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
