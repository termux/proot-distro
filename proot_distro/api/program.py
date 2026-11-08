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
from typing import Callable, Mapping, Optional, Tuple

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
from proot_distro.helpers.docker.refs import (
    InvalidReference,
    parse_reference,
    parse_tag_reference,
    with_explicit_tag,
)
from proot_distro.helpers.download import NETWORK_ERRORS
from proot_distro.names import NAME_RULE_HINT, is_valid_name
from proot_distro.paths import installed_container_names, read_container_manifest
from proot_distro.session import active_sessions

from proot_distro.api.errors import (
    AlreadyExistsError,
    AuthenticationError,
    BusyError,
    IntegrityError,
    InvalidArgumentError,
    NetworkError,
    NotFoundError,
    OperationError,
    PartialOperationError,
    UnsupportedOperationError,
)
from proot_distro.api.config import RuntimeConfig
from proot_distro.api.credentials import (
    CredentialProvider,
    CredentialRequest,
    RegistryCredentials,
)
from proot_distro.api.events import EventKind, OperationEvent
from proot_distro.api.models import (
    BackupResult,
    BuildResult,
    CacheCleanupResult,
    ContainerInfo,
    ImageInfo,
    ImageRemovalResult,
    PushResult,
    SearchHit,
    SearchResult,
    SessionInfo,
    SessionSignalResult,
    RestoreResult,
    TransferResult,
)
from proot_distro.api.process import SessionProcess


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


def _path_argument(value, *, operation: str, argument: str,
                   host_only: bool = False) -> str:
    """Validate an API path or ``container:path`` specification."""
    path_like = isinstance(value, os.PathLike)
    try:
        value = os.fspath(value)
    except TypeError:
        raise InvalidArgumentError(
            f"{argument} must be a string or path-like object.",
            operation=operation,
        ) from None
    if not isinstance(value, str) or not value:
        raise InvalidArgumentError(
            f"{argument} must be a non-empty text path.",
            operation=operation,
        )
    if "\0" in value:
        raise InvalidArgumentError(
            f"{argument} must not contain a NUL byte.",
            operation=operation,
        )
    try:
        os.fsencode(value)
    except UnicodeError:
        raise InvalidArgumentError(
            f"{argument} cannot be represented as a filesystem path.",
            operation=operation,
        ) from None
    if host_only or path_like:
        value = os.path.abspath(os.path.expanduser(value))
    return value


def _string_argument(value, *, operation: str, argument: str,
                     allow_none: bool = False, allow_empty: bool = False):
    """Validate a string carried into a session argv or environment."""
    if value is None and allow_none:
        return None
    if not isinstance(value, str) or (not value and not allow_empty):
        suffix = " or None" if allow_none else ""
        raise InvalidArgumentError(
            f"{argument} must be a non-empty string{suffix}.",
            operation=operation,
        )
    if "\0" in value:
        raise InvalidArgumentError(
            f"{argument} must not contain a NUL byte.", operation=operation
        )
    try:
        os.fsencode(value)
    except UnicodeError:
        raise InvalidArgumentError(
            f"{argument} cannot be represented by the host.",
            operation=operation,
        ) from None
    return value


def _string_list(value, *, operation: str, argument: str) -> Tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, str):
        raise InvalidArgumentError(
            f"{argument} must be an iterable of strings, not one string.",
            operation=operation,
        )
    try:
        items = tuple(value)
    except TypeError:
        raise InvalidArgumentError(
            f"{argument} must be an iterable of strings.", operation=operation
        ) from None
    return tuple(_string_argument(
        item, operation=operation, argument=f"{argument} item"
    ) for item in items)


def _write_all(fd: int, data: bytes) -> None:
    while data:
        written = os.write(fd, data)
        data = data[written:]


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


_WORKER_ERROR_TYPES = {
    "already_exists": AlreadyExistsError,
    "authentication": AuthenticationError,
    "busy": BusyError,
    "integrity": IntegrityError,
    "network": NetworkError,
    "not_found": NotFoundError,
    "operation": OperationError,
    "partial": PartialOperationError,
    "unsupported": UnsupportedOperationError,
}


def _structured_worker_error(operation: str, stdout: bytes):
    """Return a typed exception from a worker error envelope, or None."""
    try:
        payload = json.loads(stdout)
        error = payload["_error"]
        kind = error["kind"]
        message = error["message"]
        exception_type = _WORKER_ERROR_TYPES[kind]
        if not isinstance(message, str) or not message:
            raise TypeError
    except (KeyError, TypeError, ValueError, UnicodeDecodeError):
        return None
    return exception_type(message, operation=operation)


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

    if proc.returncode != 0:
        structured = _structured_worker_error(operation, stdout)
        if structured is not None:
            raise structured
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

    def _transfer(
        self,
        operation: str,
        source,
        destination,
        **options,
    ) -> TransferResult:
        source = _path_argument(
            source, operation=operation, argument="source"
        )
        destination = _path_argument(
            destination, operation=operation, argument="destination"
        )
        result = self._context.worker(operation, {
            "source": source,
            "destination": destination,
            **options,
        })
        if (result.get("source") != source
                or result.get("destination") != destination):
            raise OperationError(
                f"{operation} worker returned an invalid result.",
                operation=operation,
            )
        return TransferResult(operation, source, destination)

    def copy(
        self, source, destination, *, recursive: bool = False,
    ) -> TransferResult:
        """Copy a file or directory between host and container paths."""
        if not isinstance(recursive, bool):
            raise InvalidArgumentError(
                "recursive must be a boolean.", operation="copy"
            )
        return self._transfer(
            "copy", source, destination, move=False, recursive=recursive
        )

    def move(self, source, destination) -> TransferResult:
        """Move a file or directory between host and container paths."""
        return self._transfer(
            "move", source, destination, move=True, recursive=False
        )

    def sync(
        self,
        source,
        destination,
        *,
        checksum: bool = False,
        delete: bool = False,
    ) -> TransferResult:
        """Synchronize a file or directory to another path."""
        if not isinstance(checksum, bool):
            raise InvalidArgumentError(
                "checksum must be a boolean.", operation="sync"
            )
        if not isinstance(delete, bool):
            raise InvalidArgumentError(
                "delete must be a boolean.", operation="sync"
            )
        return self._transfer(
            "sync", source, destination, checksum=checksum, delete=delete
        )

    def backup(
        self,
        name: str,
        output,
        *,
        compression: Optional[str] = None,
    ) -> BackupResult:
        """Write a container backup to an explicit host file."""
        name = _require_name(name, operation="backup")
        output = _path_argument(
            output, operation="backup", argument="output", host_only=True
        )
        allowed = (None, "gzip", "bzip2", "xz", "zstd", "none")
        if compression not in allowed:
            raise InvalidArgumentError(
                "compression must be gzip, bzip2, xz, zstd, none, or None.",
                operation="backup",
            )
        result = self._context.worker("backup", {
            "name": name,
            "output": output,
            "compression": compression,
        })
        try:
            result_name = result["container_name"]
            result_output = result["output"]
            result_compression = result["compression"]
            size_bytes = result["size_bytes"]
            if (result_name != name or result_output != output
                    or result_compression not in allowed[1:]
                    or isinstance(size_bytes, bool)
                    or not isinstance(size_bytes, int)
                    or size_bytes < 0):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise OperationError(
                "backup worker returned an invalid result.",
                operation="backup",
            ) from None
        return BackupResult(
            name, output, result_compression, size_bytes
        )

    def restore(self, archive) -> RestoreResult:
        """Restore one container from an explicit host backup file."""
        archive = _path_argument(
            archive, operation="restore", argument="archive", host_only=True
        )
        result = self._context.worker("restore", {"archive": archive})
        name = result.get("container_name")
        if not isinstance(name, str) or not is_valid_name(name):
            raise OperationError(
                "restore worker returned an invalid container name.",
                operation="restore",
            )
        if result.get("archive") != archive:
            raise OperationError(
                "restore worker returned an invalid archive path.",
                operation="restore",
            )
        return RestoreResult(
            self._installed_container(name, "restore"), archive
        )

    def build(
        self,
        path,
        *,
        dockerfile=None,
        tags=(),
        build_args: Optional[Mapping[str, str]] = None,
        architecture: Optional[str] = None,
        target: Optional[str] = None,
        emulator=None,
        outputs=(),
        install_as: Optional[str] = None,
        no_cache: bool = False,
        credentials: Optional[RegistryCredentials] = None,
    ) -> BuildResult:
        """Build an OCI image from a Dockerfile and context directory."""
        path = _path_argument(
            path, operation="build", argument="path", host_only=True
        )
        if dockerfile is not None:
            dockerfile = _path_argument(
                dockerfile, operation="build", argument="dockerfile",
                host_only=True,
            )
        if isinstance(tags, str):
            tags = (tags,)
        try:
            tags = tuple(tags)
        except TypeError:
            raise InvalidArgumentError(
                "tags must be an iterable of image references.",
                operation="build",
            ) from None
        canonical_tags = []
        for tag in tags:
            if not isinstance(tag, str):
                raise InvalidArgumentError(
                    "every tag must be a string.", operation="build"
                )
            try:
                parse_tag_reference(tag)
            except InvalidReference as exc:
                raise InvalidArgumentError(
                    f"invalid build tag '{tag}': {exc}", operation="build"
                ) from None
            canonical_tags.append(with_explicit_tag(tag))

        if build_args is None:
            build_args = {}
        if not isinstance(build_args, Mapping):
            raise InvalidArgumentError(
                "build_args must be a mapping of strings.", operation="build"
            )
        encoded_build_args = []
        for key, value in build_args.items():
            if (not isinstance(key, str) or not key
                    or not isinstance(value, str)):
                raise InvalidArgumentError(
                    "build_args keys must be non-empty strings and values "
                    "must be strings.", operation="build"
                )
            encoded_build_args.append(f"{key}={value}")

        if architecture is not None:
            if not isinstance(architecture, str):
                raise InvalidArgumentError(
                    "architecture must be a string or None.", operation="build"
                )
            architecture = normalize_arch(architecture)
            if architecture is None:
                raise InvalidArgumentError(
                    "unsupported build architecture.", operation="build"
                )
        if target is not None and (not isinstance(target, str) or not target):
            raise InvalidArgumentError(
                "target must be a non-empty string or None.", operation="build"
            )
        if emulator is not None:
            emulator = _path_argument(
                emulator, operation="build", argument="emulator",
                host_only=True,
            )
        if isinstance(outputs, (str, os.PathLike)):
            outputs = (outputs,)
        try:
            outputs = tuple(_path_argument(
                output, operation="build", argument="output", host_only=True
            ) for output in outputs)
        except TypeError:
            raise InvalidArgumentError(
                "outputs must be an iterable of host paths.", operation="build"
            ) from None
        if install_as is not None:
            _require_name(
                install_as, operation="build", argument="install_as"
            )
        if not isinstance(no_cache, bool):
            raise InvalidArgumentError(
                "no_cache must be a boolean.", operation="build"
            )
        if credentials is not None and not isinstance(
            credentials, RegistryCredentials
        ):
            raise InvalidArgumentError(
                "credentials must be RegistryCredentials or None.",
                operation="build",
            )
        build_credentials = credentials or self._context.credentials

        result = self._context.worker("build", {
            "path": path,
            "dockerfile": dockerfile,
            "tags": canonical_tags,
            "build_args": encoded_build_args,
            "architecture": architecture,
            "target": target,
            "emulator": emulator,
            "outputs": outputs,
            "install_as": install_as,
            "no_cache": no_cache,
            "credentials": self._context.credential_payload(
                build_credentials
            ),
        })
        raw_images = result.get("images")
        if not isinstance(raw_images, list):
            raise OperationError(
                "build worker returned invalid images.", operation="build"
            )
        try:
            images = tuple(ImageInfo(**item) for item in raw_images)
        except (TypeError, ValueError):
            raise OperationError(
                "build worker returned invalid images.", operation="build"
            ) from None
        if not images:
            raise OperationError(
                "build completed without a cached image result.",
                operation="build",
            )
        if tuple(result.get("outputs", ())) != outputs:
            raise OperationError(
                "build worker returned invalid output paths.", operation="build"
            )
        installed = None
        if install_as is not None:
            installed = self._installed_container(install_as, "build")
        return BuildResult(images, outputs, installed)

    def push(
        self,
        image_ref: str,
        *,
        architecture: Optional[str] = None,
        allow_insecure: bool = False,
    ) -> PushResult:
        """Push one cached image tag to its registry."""
        if not isinstance(image_ref, str) or not image_ref:
            raise InvalidArgumentError(
                "image_ref must be a non-empty string.", operation="push"
            )
        try:
            reference = parse_tag_reference(image_ref)
        except InvalidReference as exc:
            raise InvalidArgumentError(
                f"invalid push reference: {exc}", operation="push"
            ) from None
        image_ref = with_explicit_tag(image_ref)
        if architecture is not None:
            if not isinstance(architecture, str):
                raise InvalidArgumentError(
                    "architecture must be a string or None.", operation="push"
                )
            architecture = normalize_arch(architecture)
            if architecture is None:
                raise InvalidArgumentError(
                    "unsupported push architecture.", operation="push"
                )
        if not isinstance(allow_insecure, bool):
            raise InvalidArgumentError(
                "allow_insecure must be a boolean.", operation="push"
            )
        registry_credentials = self._context.credentials_for(
            reference.registry or "docker.io", reference.repo, "push"
        )
        result = self._context.worker("push", {
            "image_ref": image_ref,
            "architecture": architecture,
            "allow_insecure": allow_insecure,
            "credentials": self._context.credential_payload(
                registry_credentials
            ),
        })
        try:
            registry = result["registry"]
            repository = result["repo"]
            tag = result["tag"]
            digest = result.get("manifest_digest") or None
            uploaded = result["bytes_uploaded"]
            if (not all(isinstance(v, str) and v
                        for v in (registry, repository, tag))
                    or (digest is not None and not isinstance(digest, str))
                    or isinstance(uploaded, bool)
                    or not isinstance(uploaded, int)
                    or uploaded < 0):
                raise ValueError
        except (KeyError, TypeError, ValueError):
            raise OperationError(
                "push worker returned an invalid result.", operation="push"
            ) from None
        return PushResult(registry, repository, tag, digest, uploaded)

    def remove_image(
        self, identifier: str, *, architecture: Optional[str] = None,
    ) -> ImageRemovalResult:
        """Remove cached image variants addressed by reference or image ID."""
        if (not isinstance(identifier, str) or not identifier
                or "\0" in identifier):
            raise InvalidArgumentError(
                "identifier must be a non-empty string without NUL bytes.",
                operation="remove_image",
            )
        if architecture is not None:
            if not isinstance(architecture, str):
                raise InvalidArgumentError(
                    "architecture must be a string or None.",
                    operation="remove_image",
                )
            architecture = normalize_arch(architecture)
            if architecture is None:
                raise InvalidArgumentError(
                    "unsupported image architecture.",
                    operation="remove_image",
                )
        result = self._context.worker("remove_image", {
            "identifier": identifier,
            "architecture": architecture,
        })
        raw_removed = result.get("removed")
        if not isinstance(raw_removed, list):
            raise OperationError(
                "remove_image worker returned an invalid result.",
                operation="remove_image",
            )
        try:
            removed = tuple(ImageInfo(**item) for item in raw_removed)
        except (TypeError, ValueError):
            raise OperationError(
                "remove_image worker returned an invalid result.",
                operation="remove_image",
            ) from None
        return ImageRemovalResult(removed)

    def clear_cache(
        self, *, orphan: bool = False, build_cache: bool = False,
    ) -> CacheCleanupResult:
        """Clear all cache data or selected orphan/build-cache data."""
        if not isinstance(orphan, bool) or not isinstance(build_cache, bool):
            raise InvalidArgumentError(
                "orphan and build_cache must be booleans.",
                operation="clear_cache",
            )
        result = self._context.worker("clear_cache", {
            "orphan": orphan,
            "build_cache": build_cache,
        })
        before = result.get("bytes_before")
        after = result.get("bytes_after")
        if (isinstance(before, bool) or not isinstance(before, int)
                or isinstance(after, bool) or not isinstance(after, int)
                or before < 0 or after < 0):
            raise OperationError(
                "clear_cache worker returned an invalid result.",
                operation="clear_cache",
            )
        mode = "build_cache" if build_cache else ("orphan" if orphan else "all")
        return CacheCleanupResult(
            mode, before, after, max(0, before - after)
        )

    def _session_options(
        self,
        operation: str,
        *,
        user,
        environment,
        binds,
        work_dir,
        redirect_ports,
        shared_home,
        shared_tmp,
        shared_x11,
        isolated,
        minimal,
        no_link2symlink,
        no_sysvipc,
        no_kill_on_exit,
        emulator,
        kernel,
        hostname,
    ) -> dict:
        user = _string_argument(
            user, operation=operation, argument="user"
        )
        if environment is None:
            environment = {}
        if not isinstance(environment, Mapping):
            raise InvalidArgumentError(
                "environment must be a mapping of strings.",
                operation=operation,
            )
        env_entries = []
        for key, value in environment.items():
            key = _string_argument(
                key, operation=operation, argument="environment key"
            )
            if "=" in key:
                raise InvalidArgumentError(
                    "environment keys must not contain '='.",
                    operation=operation,
                )
            value = _string_argument(
                value, operation=operation, argument="environment value",
                allow_empty=True,
            )
            env_entries.append(f"{key}={value}")
        binds = _string_list(
            binds, operation=operation, argument="binds"
        )
        work_dir = _string_argument(
            work_dir, operation=operation, argument="work_dir",
            allow_none=True,
        )
        emulator = (
            None if emulator is None else _path_argument(
                emulator, operation=operation, argument="emulator",
                host_only=True,
            )
        )
        kernel = _string_argument(
            kernel, operation=operation, argument="kernel", allow_none=True
        )
        hostname = _string_argument(
            hostname, operation=operation, argument="hostname", allow_none=True
        )
        flags = {
            "redirect_ports": redirect_ports,
            "shared_home": shared_home,
            "shared_tmp": shared_tmp,
            "shared_x11": shared_x11,
            "isolated": isolated,
            "minimal": minimal,
            "no_link2symlink": no_link2symlink,
            "no_sysvipc": no_sysvipc,
            "no_kill_on_exit": no_kill_on_exit,
        }
        if not all(isinstance(value, bool) for value in flags.values()):
            raise InvalidArgumentError(
                "session feature flags must be booleans.", operation=operation
            )
        if isolated and minimal:
            raise InvalidArgumentError(
                "isolated and minimal are mutually exclusive.",
                operation=operation,
            )
        return {
            "user": user,
            "env": env_entries,
            "bind": binds,
            "work_dir": work_dir,
            "emulator": emulator,
            "kernel": kernel,
            "hostname": hostname,
            **flags,
        }

    def _start_session(
        self,
        kind: str,
        container: str,
        arguments,
        *,
        stdin,
        stdout,
        stderr,
        **options,
    ) -> SessionProcess:
        operation = "start" if kind == "login" else "run"
        container = _require_name(container, operation=operation)
        arguments = _string_list(
            arguments, operation=operation, argument="arguments"
        )
        if not any(
            item.name == container for item in self.list_containers()
        ):
            raise NotFoundError(
                f"container '{container}' is not installed.",
                operation=operation,
            )
        request = json.dumps({
            "kind": kind,
            "container": container,
            "arguments": arguments,
            "options": options,
        }, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        if len(request) > 1024 * 1024:
            raise InvalidArgumentError(
                "session configuration is too large.", operation=operation
            )

        request_read = request_write = status_read = status_write = None
        try:
            request_read, request_write = os.pipe()
            status_read, status_write = os.pipe()
        except OSError as exc:
            for fd in (request_read, request_write, status_read, status_write):
                if fd is not None:
                    try:
                        os.close(fd)
                    except OSError:
                        pass
            raise OperationError(
                f"cannot create session control pipes: {exc}",
                operation=operation,
            ) from exc
        env = _worker_environment(self.config)
        env["_PROOT_DISTRO_SESSION_REQUEST_FD"] = str(request_read)
        env["_PROOT_DISTRO_SESSION_STATUS_FD"] = str(status_write)
        try:
            process = subprocess.Popen(
                [sys.executable, "-m", "proot_distro.api._session_worker"],
                stdin=stdin,
                stdout=stdout,
                stderr=stderr,
                env=env,
                pass_fds=(request_read, status_write),
            )
        except (OSError, ValueError) as exc:
            for fd in (request_read, request_write, status_read, status_write):
                try:
                    os.close(fd)
                except OSError:
                    pass
            raise OperationError(
                f"cannot start the session worker: {exc}",
                operation=operation,
            ) from exc

        os.close(request_read)
        os.close(status_write)
        try:
            _write_all(request_write, request)
        except OSError as exc:
            process.kill()
            process.wait()
            os.close(status_read)
            raise OperationError(
                f"cannot configure the session worker: {exc}",
                operation=operation,
            ) from exc
        finally:
            os.close(request_write)

        status = bytearray()
        try:
            while len(status) <= 65536:
                chunk = os.read(status_read, 65537 - len(status))
                if not chunk:
                    break
                status.extend(chunk)
        except KeyboardInterrupt:
            try:
                process.send_signal(signal.SIGINT)
            except OSError:
                pass
            process.wait()
            raise
        except OSError as exc:
            process.kill()
            process.wait()
            raise OperationError(
                f"cannot read session startup status: {exc}",
                operation=operation,
            ) from exc
        finally:
            os.close(status_read)
        if len(status) > 65536:
            process.kill()
            process.wait()
            raise OperationError(
                "session worker returned an oversized setup status.",
                operation=operation,
            )
        if status:
            process.wait()
            try:
                payload = json.loads(bytes(status))
                message = payload["error"]
                if not isinstance(message, str):
                    raise TypeError
            except (KeyError, TypeError, ValueError, UnicodeDecodeError):
                message = "session worker returned an invalid setup error."
            raise OperationError(message, operation=operation)
        return SessionProcess(
            self, process, container=container, kind=kind
        )

    def start(
        self,
        container: str,
        arguments=(),
        *,
        user: str = "root",
        environment: Optional[Mapping[str, str]] = None,
        binds=(),
        work_dir: Optional[str] = None,
        redirect_ports: bool = False,
        shared_home: bool = False,
        shared_tmp: bool = False,
        shared_x11: bool = False,
        isolated: bool = False,
        minimal: bool = False,
        no_link2symlink: bool = False,
        no_sysvipc: bool = False,
        no_kill_on_exit: bool = False,
        emulator=None,
        kernel: Optional[str] = None,
        hostname: Optional[str] = None,
        stdin=None,
        stdout=None,
        stderr=None,
    ) -> SessionProcess:
        """Start a login shell or shell-wrapped command inside a container."""
        options = self._session_options(
            "start", user=user, environment=environment, binds=binds,
            work_dir=work_dir, redirect_ports=redirect_ports,
            shared_home=shared_home, shared_tmp=shared_tmp,
            shared_x11=shared_x11, isolated=isolated, minimal=minimal,
            no_link2symlink=no_link2symlink, no_sysvipc=no_sysvipc,
            no_kill_on_exit=no_kill_on_exit, emulator=emulator,
            kernel=kernel, hostname=hostname,
        )
        return self._start_session(
            "login", container, arguments, stdin=stdin, stdout=stdout,
            stderr=stderr, **options,
        )

    def run(
        self,
        container: str,
        arguments=(),
        *,
        user: str = "root",
        environment: Optional[Mapping[str, str]] = None,
        binds=(),
        work_dir: Optional[str] = None,
        redirect_ports: bool = False,
        shared_home: bool = False,
        shared_tmp: bool = False,
        shared_x11: bool = False,
        isolated: bool = False,
        minimal: bool = False,
        no_link2symlink: bool = False,
        no_sysvipc: bool = False,
        no_kill_on_exit: bool = False,
        emulator=None,
        kernel: Optional[str] = None,
        hostname: Optional[str] = None,
        stdin=None,
        stdout=None,
        stderr=None,
    ) -> SessionProcess:
        """Start the image Entrypoint/Cmd, optionally replacing Cmd arguments."""
        options = self._session_options(
            "run", user=user, environment=environment, binds=binds,
            work_dir=work_dir, redirect_ports=redirect_ports,
            shared_home=shared_home, shared_tmp=shared_tmp,
            shared_x11=shared_x11, isolated=isolated, minimal=minimal,
            no_link2symlink=no_link2symlink, no_sysvipc=no_sysvipc,
            no_kill_on_exit=no_kill_on_exit, emulator=emulator,
            kernel=kernel, hostname=hostname,
        )
        return self._start_session(
            "run", container, arguments,
            stdin=stdin, stdout=stdout, stderr=stderr,
            **options,
        )

    def kill_session(
        self, target, *, signal_name="TERM",
    ) -> SessionSignalResult:
        """Signal a tracked session PID or every session of a container."""
        if isinstance(target, bool):
            target = None
        if isinstance(target, int) and target > 0:
            target_text = str(target)
        elif isinstance(target, str) and target:
            if not (target.isascii() and target.isdigit()):
                _require_name(target, operation="kill_session")
            target_text = target
        else:
            raise InvalidArgumentError(
                "target must be a positive PID or container name.",
                operation="kill_session",
            )
        try:
            if isinstance(signal_name, bool):
                raise ValueError
            if isinstance(signal_name, int):
                selected_signal = signal.Signals(signal_name)
            elif isinstance(signal_name, signal.Signals):
                selected_signal = signal_name
            elif isinstance(signal_name, str):
                raw = signal_name.strip().upper()
                if raw.isdigit():
                    selected_signal = signal.Signals(int(raw))
                else:
                    if not raw.startswith("SIG"):
                        raw = "SIG" + raw
                    selected_signal = signal.Signals[raw]
            else:
                raise ValueError
        except (KeyError, ValueError):
            raise InvalidArgumentError(
                f"invalid signal '{signal_name}'.", operation="kill_session"
            ) from None
        result = self._context.worker("kill_session", {
            "target": target_text,
            "signal": selected_signal.name,
        })
        pids = result.get("pids")
        if (not isinstance(pids, list)
                or any(isinstance(pid, bool) or not isinstance(pid, int)
                       or pid <= 0 for pid in pids)):
            raise OperationError(
                "kill_session worker returned an invalid result.",
                operation="kill_session",
            )
        return SessionSignalResult(
            target_text, selected_signal.name, tuple(pids)
        )

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
