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

import codecs
import io
import json
import math
import os
import selectors
import signal
import socket
import subprocess
import threading
import time
from dataclasses import dataclass
from typing import (
    Any,
    BinaryIO,
    Iterable,
    IO,
    Mapping,
    Optional,
    Tuple,
    Union,
)

from proot_distro._bootstrap import private_module_command
from proot_distro.arch import normalize_arch
from proot_distro.constants import (
    IS_TERMUX,
    TERMUX_APP_PACKAGE,
    TERMUX_HOME,
    TERMUX_PREFIX,
)
from proot_distro.helpers.docker.search import (
    SEARCH_DEFAULT_LIMIT,
    SEARCH_LIMIT_MAX,
    search_images,
)
from proot_distro.helpers.docker.refs import (
    InvalidReference,
    parse_reference,
    parse_tag_reference,
    registry_key,
    with_explicit_tag,
)
from proot_distro.helpers.download import NETWORK_ERRORS
from proot_distro.message import printable_message
from proot_distro.names import NAME_RULE_HINT, is_valid_name
from proot_distro.paths import container_from_spec

from proot_distro.api.errors import (
    AlreadyExistsError,
    AuthenticationError,
    BusyError,
    IntegrityError,
    InvalidArgumentError,
    NetworkError,
    NotFoundError,
    OperationError,
    OperationCancelledError,
    PartialOperationError,
    SessionStartupTimeoutError,
    UnsupportedOperationError,
)
from proot_distro.api.cancellation import CancellationToken
from proot_distro.api.config import RuntimeConfig
from proot_distro.api.credentials import (
    CredentialProvider,
    CredentialRequest,
    RegistryCredentials,
)
from proot_distro.api.events import EventHandler, EventKind, OperationEvent
from proot_distro.api.models import (
    BackupResult,
    BuildResult,
    CacheCleanupResult,
    ContainerInfo,
    DetachedSession,
    ImageInfo,
    ImageRemovalResult,
    PushResult,
    SearchHit,
    SearchResult,
    SessionInfo,
    SessionCommand,
    SessionSignalResult,
    RestoreResult,
    TransferResult,
)
from proot_distro.api.process import SessionProcess


_PathArgument = Union[str, os.PathLike[str]]
_PathOrBinaryStream = Union[str, os.PathLike[str], BinaryIO]
_BuildOutputs = Union[
    str,
    os.PathLike[str],
    BinaryIO,
    Iterable[Union[str, os.PathLike[str], BinaryIO]],
]
_SessionStdio = Optional[Union[int, IO[Any]]]
_DetachedOutput = Optional[Union[str, os.PathLike[str], int]]
_SignalArgument = Union[int, str, signal.Signals]

_MAX_WORKER_ERROR_BYTES = 64 * 1024
_MAX_WORKER_EVENT_BYTES = 64 * 1024
_MAX_WORKER_RESULT_BYTES = 16 * 1024 * 1024
_MAX_SESSION_STATUS_BYTES = 64 * 1024
_MAX_CREDENTIAL_FRAME_BYTES = 1024 * 1024
# What _api_worker.py reads from its standard input (its _MAX_INPUT_BYTES).
_MAX_WORKER_REQUEST_BYTES = 1024 * 1024
# How long a cancelled worker may take to clean up before it is killed.
_WORKER_CANCEL_GRACE_SECONDS = 10.0
_WORKER_STOP_GRACE_SECONDS = 5.0


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


def _host_path(value: str) -> str:
    """Return *value* as an absolute, "~"-expanded host path.

    A trailing separator is kept: copy and sync treat ``dir/`` and ``dir``
    differently, so it is part of the caller's meaning.
    """
    expanded = os.path.expanduser(value)
    absolute = os.path.abspath(expanded)
    if expanded.endswith(os.sep) and not absolute.endswith(os.sep):
        absolute += os.sep
    return absolute


def _binary_stream(value, *, operation: str, argument: str,
                   readable: bool = False, writable: bool = False) -> BinaryIO:
    """Validate a caller-owned sequential binary stream without consuming it."""
    method_name = "read" if readable else "write"
    capability_name = "readable" if readable else "writable"
    if isinstance(value, io.TextIOBase) or not callable(
        getattr(value, method_name, None)
    ):
        raise InvalidArgumentError(
            f"{argument} must be a binary {capability_name} stream.",
            operation=operation,
        )
    capability = getattr(value, capability_name, None)
    if callable(capability):
        try:
            capable = capability()
        except (OSError, ValueError) as exc:
            raise InvalidArgumentError(
                f"{argument} is not an open {capability_name} stream: {exc}",
                operation=operation,
            ) from None
        if not capable:
            raise InvalidArgumentError(
                f"{argument} must be a binary {capability_name} stream.",
                operation=operation,
            )
    return value


def _path_or_stream(value, *, operation: str, argument: str,
                    readable: bool = False, writable: bool = False):
    """Return ``(path, None)`` or ``(None, stream)`` for a public argument."""
    if isinstance(value, (str, os.PathLike)):
        return _path_argument(
            value, operation=operation, argument=argument, host_only=True
        ), None
    return None, _binary_stream(
        value, operation=operation, argument=argument,
        readable=readable, writable=writable,
    )


def _detached_output(value, *, operation: str, argument: str,
                     allow_stdout: bool = False):
    """Validate an append-only detached output path or stdio sentinel."""
    if value is None:
        return None
    # Only a true int is a subprocess sentinel: -3.0 compares equal to
    # DEVNULL and True to 1, and neither was meant as one.
    is_int = isinstance(value, int) and not isinstance(value, bool)
    if is_int and value == subprocess.DEVNULL:
        return None
    if is_int and allow_stdout and value == subprocess.STDOUT:
        return "stdout"
    if isinstance(value, (int, float)):
        raise InvalidArgumentError(
            f"{argument} must be a host path, subprocess.DEVNULL, "
            f"{'subprocess.STDOUT, ' if allow_stdout else ''}or None.",
            operation=operation,
        )
    return _path_argument(
        value, operation=operation, argument=argument, host_only=True
    )


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


def _container_info(item) -> ContainerInfo:
    """Validate one container record a worker returned."""
    if not isinstance(item, dict):
        raise TypeError
    info = ContainerInfo(**item)
    if not isinstance(info.name, str) or not is_valid_name(info.name):
        raise ValueError
    for value in (info.image_reference, info.architecture, info.image_id):
        if value is not None and not isinstance(value, str):
            raise TypeError
    return info


def _reported_container(result: dict, operation: str,
                        name: Optional[str] = None) -> ContainerInfo:
    """Return the container an operation's worker reported it left behind.

    Workers describe the container they installed, renamed, reset or
    restored themselves; listing every container again in a second worker
    cost a whole interpreter start per call.
    """
    try:
        info = _container_info(result.get("container"))
        if name is not None and info.name != name:
            raise ValueError
    except (TypeError, ValueError):
        raise OperationError(
            f"{operation} worker returned an invalid container record.",
            operation=operation,
        ) from None
    return info


def _write_all(fd: int, data: bytes) -> None:
    while data:
        written = os.write(fd, data)
        data = data[written:]


def _signal_argument(value, *, operation: str) -> signal.Signals:
    """Normalize a signal number or name for a public session method."""
    try:
        if isinstance(value, bool):
            raise ValueError
        if isinstance(value, int):
            return signal.Signals(value)
        if isinstance(value, signal.Signals):
            return value
        if isinstance(value, str):
            raw = value.strip().upper()
            if raw.isdigit():
                return signal.Signals(int(raw))
            if not raw.startswith("SIG"):
                raw = "SIG" + raw
            return signal.Signals[raw]
        raise ValueError
    except (KeyError, ValueError):
        raise InvalidArgumentError(
            f"invalid signal '{value}'.", operation=operation
        ) from None


def _cancel_control(
    cancel_event: Optional[CancellationToken], *, operation: str,
    subject: str = "operation",
) -> Optional[CancellationToken]:
    """Validate a cooperative cancellation token and reject a pre-set one."""
    if (cancel_event is not None
            and not callable(getattr(cancel_event, "is_set", None))):
        raise InvalidArgumentError(
            "cancel_event must provide an is_set() method or be None.",
            operation=operation,
        )
    if _cancellation_requested(cancel_event, operation):
        raise OperationCancelledError(
            f"{subject} was cancelled.", operation=operation
        )
    return cancel_event


def _cancellation_requested(
    cancel_event: Optional[CancellationToken], operation: str,
) -> bool:
    """Read and validate one cancellation state from a public token."""
    if cancel_event is None:
        return False
    cancelled = cancel_event.is_set()
    if not isinstance(cancelled, bool):
        raise InvalidArgumentError(
            "cancel_event.is_set() must return a boolean.",
            operation=operation,
        )
    return cancelled


def _startup_controls(timeout, cancel_event, *, operation: str):
    """Validate foreground session startup timeout and cancellation token."""
    if timeout is not None:
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout < 0):
            raise InvalidArgumentError(
                "startup_timeout must be a finite non-negative number or "
                "None.", operation=operation,
            )
        timeout = float(timeout)
    return timeout, _cancel_control(
        cancel_event, operation=operation, subject="session setup"
    )


def _stop_starting_session(process) -> None:
    """Stop and reap a session worker that has not completed its handshake.

    SIGKILL cannot be ignored, so the wait ends; a Ctrl-C during it is
    deferred until the child is reaped rather than leaving it behind.
    """
    try:
        process.kill()
    except OSError:
        pass
    interrupt = None
    while True:
        try:
            process.wait()
            break
        except KeyboardInterrupt as exc:
            interrupt = interrupt or exc
        except OSError:
            break
    if interrupt is not None:
        raise interrupt


def _interrupt_starting_session(process) -> None:
    """Let an interrupted session worker clean up, within limits.

    The worker gets the caller's SIGINT and normally reports and exits at
    once. If it does not within the grace period -- it may already have
    become proot, which ignores SIGINT, and would then run until the
    guest command ends -- or the caller presses Ctrl-C again, it is
    killed. Either way it is reaped before the caller's interrupt goes on.
    """
    try:
        process.send_signal(signal.SIGINT)
    except OSError:
        pass
    try:
        process.wait(timeout=_WORKER_STOP_GRACE_SECONDS)
        return
    except (subprocess.TimeoutExpired, KeyboardInterrupt):
        pass
    except OSError:
        return
    try:
        _stop_starting_session(process)
    except KeyboardInterrupt:
        # The caller's first interrupt is the one that goes on.
        pass


def _read_session_startup(
    fd: int,
    operation: str,
    event_handler: Optional[EventHandler],
    timeout: Optional[float],
    cancel_event,
) -> Optional[str]:
    """Read framed setup events until exec closes *fd*; return an error."""
    deadline = None if timeout is None else time.monotonic() + timeout
    buffer = bytearray()
    setup_error = None

    def handle_frame(raw: bytes) -> None:
        nonlocal setup_error
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError
            if "error" in payload:
                message = payload["error"]
                if not isinstance(message, str) or not message:
                    raise TypeError
                setup_error = _SetupError(message)
                kind = payload.get("kind", "operation")
                if kind in _WORKER_ERROR_TYPES:
                    setup_error.kind = kind
                return
            event = payload["event"]
            if not isinstance(event, dict):
                raise TypeError
            kind = EventKind(event["kind"])
            message = event["message"]
            if kind not in (EventKind.INFO, EventKind.WARNING):
                raise ValueError
            if not isinstance(message, str):
                raise TypeError
            phase = event.get("phase")
            resource = event.get("resource")
            if phase is not None and not isinstance(phase, str):
                raise TypeError
            if resource is not None and not isinstance(resource, str):
                raise TypeError
        except (
            KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError,
        ):
            raise OperationError(
                "session worker returned an invalid setup status.",
                operation=operation,
            ) from None
        if event_handler is not None:
            event_handler(OperationEvent(
                operation, kind, printable_message(message),
                phase=phase, resource=resource,
            ))

    while True:
        if _cancellation_requested(cancel_event, operation):
            raise OperationCancelledError(
                "session setup was cancelled.", operation=operation
            )
        wait = None
        if deadline is not None:
            wait = max(0.0, deadline - time.monotonic())
        if cancel_event is not None:
            wait = 0.1 if wait is None else min(wait, 0.1)
        try:
            readable = _wait_readable([fd], wait)
        except OSError as exc:
            raise OperationError(
                f"cannot wait for session startup status: {exc}",
                operation=operation,
            ) from exc
        if not readable:
            if deadline is not None and time.monotonic() >= deadline:
                raise SessionStartupTimeoutError(
                    f"session setup exceeded {timeout:g} seconds.",
                    operation=operation,
                )
            continue
        try:
            chunk = os.read(fd, 65536)
        except OSError as exc:
            raise OperationError(
                f"cannot read session startup status: {exc}",
                operation=operation,
            ) from exc
        if not chunk:
            if buffer:
                raise OperationError(
                    "session worker returned a truncated setup status.",
                    operation=operation,
                )
            return setup_error
        buffer.extend(chunk)
        if (len(buffer) > _MAX_SESSION_STATUS_BYTES
                and b"\n" not in buffer):
            raise OperationError(
                "session worker returned an oversized setup status.",
                operation=operation,
            )
        while b"\n" in buffer:
            raw, _, remainder = buffer.partition(b"\n")
            buffer[:] = remainder
            if not raw or len(raw) > _MAX_SESSION_STATUS_BYTES:
                raise OperationError(
                    "session worker returned an invalid setup status.",
                    operation=operation,
                )
            handle_frame(bytes(raw))


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
    "invalid_argument": InvalidArgumentError,
    "integrity": IntegrityError,
    "network": NetworkError,
    "not_found": NotFoundError,
    "operation": OperationError,
    "partial": PartialOperationError,
    "unsupported": UnsupportedOperationError,
}


class _SetupError(str):
    """A session-setup failure message that remembers its error kind."""

    kind = "operation"


def _cancellation_note(streams) -> str:
    """' ' and what a cancelled worker said the cancel cost, or ''.

    A reset or a restore that removed the old container before it was
    cancelled leaves a note of how to get it back
    (operation_errors.attach_recovery), in an error envelope of kind
    "cancelled", for the cancellation's message.
    """
    try:
        error = json.loads(streams[0])["_error"]
        if error["kind"] != "cancelled":
            return ""
        note = error["message"]
    except (KeyError, IndexError, TypeError, ValueError, UnicodeDecodeError,
            RecursionError):
        return ""
    return f" {note}" if isinstance(note, str) and note else ""


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
    except (
        KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError,
    ):
        return None
    return exception_type(message, operation=operation)


def _reject_credential_request(sock) -> None:
    try:
        sock.sendall(b'{"error":"credential provider failed"}\n')
    except OSError:
        pass


def _handle_credential_request(raw, sock, provider, operation: str) -> None:
    """Validate and answer one worker credential request frame."""
    try:
        request_data = json.loads(raw)
        registry = request_data["registry"]
        repository = request_data["repository"]
        actions = request_data["actions"]
        if not all(isinstance(value, str) and value for value in (
            registry, repository, actions,
        )):
            raise TypeError
        registry = registry_key(registry)
    except (
        KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError,
    ):
        _reject_credential_request(sock)
        raise OperationError(
            "credential worker returned an invalid request.",
            operation=operation,
        ) from None
    try:
        credentials = provider(CredentialRequest(
            registry, repository, operation
        ))
        if credentials is not None and not isinstance(
            credentials, RegistryCredentials
        ):
            raise InvalidArgumentError(
                "credential_provider must return RegistryCredentials or None.",
                operation=operation,
            )
    except BaseException:
        _reject_credential_request(sock)
        raise
    if (credentials is not None and credentials.registry is not None
            and credentials.registry != registry):
        # Scoped to another registry: never sent here, whoever chose them.
        credentials = None
    response = {
        "credentials": (
            None if credentials is None else {
                "username": credentials.username,
                "password": credentials.password,
            }
        ),
    }
    sock.sendall(json.dumps(
        response, ensure_ascii=True, separators=(",", ":")
    ).encode("ascii") + b"\n")


def _serve_credential_requests(sock, provider, operation: str):
    """Serve worker credential requests; return a provider exception or None."""
    buffer = bytearray()
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            if buffer:
                return OperationError(
                    "credential worker returned a truncated request.",
                    operation=operation,
                )
            return None
        buffer.extend(chunk)
        if len(buffer) > _MAX_CREDENTIAL_FRAME_BYTES:
            error = OperationError(
                "credential worker request is too large.", operation=operation
            )
            _reject_credential_request(sock)
            return error
        while b"\n" in buffer:
            raw, _, remainder = buffer.partition(b"\n")
            buffer[:] = remainder
            try:
                _handle_credential_request(raw, sock, provider, operation)
            except BaseException as exc:
                return exc


def _operation_event(operation: str, raw: bytes) -> OperationEvent:
    """Validate one framed worker event and construct its public model.

    The message is escaped (printable_message). Build output does not
    come this way: it is a RUN step's own output, passed on as it came.
    """
    try:
        event = json.loads(raw)
        kind = EventKind(event["kind"])
        message = event["message"]
        if not isinstance(message, str):
            raise TypeError
        current = event.get("current")
        total = event.get("total")
        unit = event.get("unit")
        phase = event.get("phase")
        resource = event.get("resource")
        for value in (current, total):
            if value is not None and (
                isinstance(value, bool) or not isinstance(value, int)
            ):
                raise TypeError
        for value in (unit, phase, resource):
            if value is not None and not isinstance(value, str):
                raise TypeError
    except (
        KeyError, TypeError, ValueError, UnicodeDecodeError, RecursionError,
    ):
        raise OperationError(
            f"{operation} worker returned an invalid event.",
            operation=operation,
        ) from None
    return OperationEvent(
        operation, kind, printable_message(message), current, total, unit,
        phase, resource,
    )


def _wait_readable(readers, timeout):
    """Return the *readers* that are ready, waiting up to *timeout* seconds.

    ``selectors`` is used instead of ``select.select`` because the latter
    raises ValueError for any descriptor numbered 1024 or higher, which an
    embedding application with many open files can easily hand us.
    """
    with selectors.DefaultSelector() as selector:
        for reader in readers:
            selector.register(reader, selectors.EVENT_READ)
        return [key.fileobj for key, _ in selector.select(timeout)]


def _communicate_bounded(proc, request: bytes):
    """Like ``proc.communicate(request)`` but with bounded memory.

    The worker's result is capped at ``_MAX_WORKER_RESULT_BYTES``: one byte
    more than that is kept, so the caller's size check still fires, and the
    rest is read and discarded so the worker never blocks on a full pipe.
    Only the last ``_MAX_WORKER_ERROR_BYTES`` of standard error are kept,
    matching how a failure message is built from it. Process stand-ins
    without real pipes fall back to their own ``communicate``.
    """
    if (getattr(proc, "stdin", None) is None
            or getattr(proc, "stdout", None) is None
            or getattr(proc, "stderr", None) is None):
        return proc.communicate(request)

    out = bytearray()
    err = bytearray()

    def feed() -> None:
        try:
            proc.stdin.write(request)
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def drain_stdout() -> None:
        while True:
            chunk = proc.stdout.read(65536)
            if not chunk:
                return
            room = _MAX_WORKER_RESULT_BYTES + 1 - len(out)
            if room > 0:
                out.extend(chunk[:room])

    def drain_stderr() -> None:
        while True:
            chunk = proc.stderr.read(65536)
            if not chunk:
                return
            err.extend(chunk)
            if len(err) > _MAX_WORKER_ERROR_BYTES:
                del err[:-_MAX_WORKER_ERROR_BYTES]

    threads = [
        threading.Thread(target=target, daemon=True)
        for target in (feed, drain_stdout, drain_stderr)
    ]
    for thread in threads:
        thread.start()
    try:
        for thread in threads:
            thread.join()
    finally:
        proc.stdout.close()
        proc.stderr.close()
    proc.wait()
    return bytes(out), bytes(err)


def _communicate_with_channels(
    proc,
    request: bytes,
    event_fd: int,
    event_handler,
    operation: str,
    credential_sock=None,
    credential_provider=None,
    build_output_fd=None,
    build_output_resource=None,
    cancel_event=None,
):
    """Drain process pipes while serving live channels in the caller thread."""
    result = {}
    # Set by the communicate thread itself when it is finished. Waiting on
    # this instead of Thread.join()/is_alive() matters: a KeyboardInterrupt
    # during join() can leave CPython 3.12 believing a still-running thread
    # has stopped, which would let us raise before the worker is reaped.
    finished = threading.Event()

    def communicate():
        try:
            result["streams"] = _communicate_bounded(proc, request)
        except BaseException as exc:
            result["error"] = exc
        finally:
            finished.set()

    thread = threading.Thread(target=communicate, daemon=True)
    thread.start()
    event_buffer = bytearray()
    credential_buffer = bytearray()
    build_decoder = codecs.getincrementaldecoder("utf-8")(
        "backslashreplace"
    )
    provider_error = None
    event_open = True
    credential_open = credential_sock is not None
    build_output_open = build_output_fd is not None
    cancelled = False
    caller_interrupt = None
    # Stopping a worker is a two-step affair shared by cancellation and
    # Ctrl-C: SIGINT first so it can clean up, SIGKILL once the grace
    # period passes (or at once on a repeated Ctrl-C) so a worker stuck in
    # an uninterruptible call cannot leave the caller hanging forever.
    stop_started = None
    killed = False

    def interrupt_worker() -> None:
        nonlocal stop_started
        if stop_started is None:
            stop_started = time.monotonic()
            _signal_worker_group(proc, signal.SIGINT)

    def kill_worker() -> None:
        nonlocal killed
        if not killed:
            killed = True
            _signal_worker_group(proc, signal.SIGKILL)

    def escalate_if_due() -> None:
        if (stop_started is not None and not killed
                and time.monotonic() - stop_started
                > _WORKER_CANCEL_GRACE_SECONDS):
            kill_worker()

    def note_interrupt(exc: KeyboardInterrupt) -> None:
        """Record Ctrl-C: the first asks the worker to stop, more force it."""
        nonlocal caller_interrupt
        if caller_interrupt is None:
            caller_interrupt = exc
            if stop_started is None:
                interrupt_worker()
            else:
                kill_worker()
        else:
            kill_worker()

    def close_channels() -> None:
        nonlocal event_open, credential_open, build_output_open
        if event_open:
            os.close(event_fd)
            event_open = False
        if credential_open:
            credential_sock.close()
            credential_open = False
        if build_output_open:
            os.close(build_output_fd)
            build_output_open = False

    def stop_while_draining() -> None:
        """Stop the worker after a failure on this side, still reading it.

        The worker reports events while it unwinds from the SIGINT, and
        a write to a full pipe blocks. Waiting on the process alone left
        a worker whose event pipe had filled behind a slow handler stuck
        in that write until the SIGKILL, so its staged state -- the
        temporary beside a path output -- was never removed. What is
        read here is thrown away: the handler that failed is not called
        again. The credential channel is closed instead, so a request
        made during cleanup fails at once rather than waits for an answer.

        A stop already under way (a cancel the handler then failed
        during) keeps its SIGINT and its deadline: a second SIGINT could
        cut the worker's cleanup short.
        """
        nonlocal event_open, credential_open, build_output_open
        if credential_open:
            credential_sock.close()
            credential_open = False
        if stop_started is None:
            interrupt_worker()
            grace = _WORKER_STOP_GRACE_SECONDS
        else:
            grace = _WORKER_CANCEL_GRACE_SECONDS
        deadline = stop_started + grace
        while not finished.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                kill_worker()
                return
            readers = []
            if event_open:
                readers.append(event_fd)
            if build_output_open:
                readers.append(build_output_fd)
            if not readers:
                finished.wait(min(remaining, 0.1))
                continue
            for fd in _wait_readable(readers, min(remaining, 0.1)):
                try:
                    chunk = os.read(fd, 65536)
                except OSError:
                    chunk = b""
                if chunk:
                    continue
                os.close(fd)
                if fd == event_fd:
                    event_open = False
                else:
                    build_output_open = False

    try:
        while event_open or credential_open or build_output_open:
            try:
                if (caller_interrupt is None and not cancelled
                        and _cancellation_requested(cancel_event, operation)):
                    cancelled = True
                    interrupt_worker()
                escalate_if_due()
                readers = []
                if event_open:
                    readers.append(event_fd)
                if credential_open:
                    readers.append(credential_sock)
                if build_output_open:
                    readers.append(build_output_fd)
                readable = _wait_readable(
                    readers,
                    0.1 if (cancel_event is not None
                            or stop_started is not None) else None,
                )
                if event_open and event_fd in readable:
                    chunk = os.read(event_fd, 65536)
                    if not chunk:
                        if event_buffer:
                            raise OperationError(
                                f"{operation} worker returned a truncated "
                                "event.",
                                operation=operation,
                            )
                        os.close(event_fd)
                        event_open = False
                    else:
                        event_buffer.extend(chunk)
                        while b"\n" in event_buffer:
                            raw, _, remainder = event_buffer.partition(b"\n")
                            event_buffer[:] = remainder
                            if not raw or len(raw) > _MAX_WORKER_EVENT_BYTES:
                                raise OperationError(
                                    f"{operation} worker returned an invalid "
                                    "event.",
                                    operation=operation,
                                )
                            event = _operation_event(operation, bytes(raw))
                            if event_handler is not None:
                                event_handler(event)
                        if len(event_buffer) > _MAX_WORKER_EVENT_BYTES:
                            raise OperationError(
                                f"{operation} worker returned an oversized "
                                "event.",
                                operation=operation,
                            )
                if build_output_open and build_output_fd in readable:
                    chunk = os.read(build_output_fd, 65536)
                    if not chunk:
                        message = build_decoder.decode(b"", final=True)
                        if message and event_handler is not None:
                            event_handler(OperationEvent(
                                operation, EventKind.INFO, message,
                                phase="build-output",
                                resource=build_output_resource,
                            ))
                        os.close(build_output_fd)
                        build_output_open = False
                    elif event_handler is not None:
                        message = build_decoder.decode(chunk)
                        if message:
                            event_handler(OperationEvent(
                                operation, EventKind.INFO, message,
                                phase="build-output",
                                resource=build_output_resource,
                            ))
                    else:
                        build_decoder.decode(chunk)
                if credential_open and credential_sock in readable:
                    try:
                        chunk = credential_sock.recv(65536)
                    except OSError as exc:
                        chunk = b""
                        if not cancelled and provider_error is None:
                            provider_error = OperationError(
                                f"credential worker channel failed: {exc}",
                                operation=operation,
                            )
                    if not chunk:
                        if credential_buffer and provider_error is None:
                            provider_error = OperationError(
                                "credential worker returned a truncated "
                                "request.",
                                operation=operation,
                            )
                        credential_sock.close()
                        credential_open = False
                    else:
                        credential_buffer.extend(chunk)
                        if len(credential_buffer) > _MAX_CREDENTIAL_FRAME_BYTES:
                            _reject_credential_request(credential_sock)
                            provider_error = OperationError(
                                "credential worker request is too large.",
                                operation=operation,
                            )
                            credential_sock.close()
                            credential_open = False
                        while credential_open and b"\n" in credential_buffer:
                            raw, _, remainder = credential_buffer.partition(
                                b"\n"
                            )
                            credential_buffer[:] = remainder
                            try:
                                _handle_credential_request(
                                    raw, credential_sock, credential_provider,
                                    operation,
                                )
                            except BaseException as exc:
                                provider_error = exc
                                credential_sock.close()
                                credential_open = False
            except KeyboardInterrupt as exc:
                # Keep the channels open after forwarding SIGINT. The worker's
                # own interruption handlers report their final event before
                # cleaning staged state. Closing the event pipe here turned
                # that report into BrokenPipeError and skipped the cleanup.
                note_interrupt(exc)
                # The handler may itself have raised KeyboardInterrupt. Do
                # not invoke it again while draining the worker's cleanup.
                event_handler = None
    except BaseException:
        # An event handler, the credential provider or the protocol failed.
        # Stop the worker, reading what it still sends, then wait for the
        # communicate thread to reap it -- in slices, not with
        # Thread.join(), for the reason given where `finished` is made.
        # Ctrl-C meanwhile kills it and wins.
        interrupt = None
        try:
            stop_while_draining()
        except KeyboardInterrupt as exc:
            interrupt = exc
            kill_worker()
        close_channels()
        while not finished.is_set():
            try:
                finished.wait(0.1)
            except KeyboardInterrupt as exc:
                interrupt = interrupt or exc
                kill_worker()
        if interrupt is not None:
            raise interrupt
        raise
    finally:
        close_channels()
    # The channels are closed but the worker may still be finishing (or
    # stuck). Wait in short slices so Ctrl-C and the kill escalation keep
    # working here too, instead of blocking uninterruptibly in join().
    while not finished.is_set():
        try:
            finished.wait(0.1)
        except KeyboardInterrupt as exc:
            note_interrupt(exc)
        escalate_if_due()
    if caller_interrupt is not None:
        raise caller_interrupt
    if provider_error is not None:
        raise provider_error
    # A cancel that raced the worker's completion must not mask a
    # committed result: when the worker exited 0 the operation happened
    # (container installed, session started), so report that, not
    # "cancelled", or the caller retries or leaks the new state.
    if cancelled and getattr(proc, "returncode", None) != 0:
        note = _cancellation_note(result.get("streams"))
        raise OperationCancelledError(
            f"{operation} was cancelled.{note}", operation=operation
        )
    if "error" in result:
        raise result["error"]
    return result["streams"]


def _signal_worker_group(proc, sig) -> bool:
    """Signal the worker's process group unless the worker is reaped.

    The group ID is the worker's PID. Once the worker has been reaped and
    its group has emptied, that number is free for the kernel to hand out
    again, so a signal sent to it then could reach an unrelated process
    group. A worker known to be reaped is therefore never signalled.
    Returns whether the signal was sent.
    """
    if getattr(proc, "returncode", None) is not None:
        return False
    try:
        os.killpg(proc.pid, sig)
    except OSError:
        return False
    return True


def _stop_worker(proc) -> None:
    """Interrupt a failed worker for cleanup, then kill it if it stalls."""
    if not _signal_worker_group(proc, signal.SIGINT):
        return
    try:
        proc.wait(timeout=_WORKER_STOP_GRACE_SECONDS)
        return
    except (AttributeError, TypeError):
        # Lightweight process stand-ins used by embedders may not expose the
        # subprocess timeout contract. They still need deterministic teardown.
        pass
    except subprocess.TimeoutExpired:
        pass
    _signal_worker_group(proc, signal.SIGKILL)


def _write_stream_chunk(stream, chunk: bytes, operation: str) -> None:
    """Write all of *chunk*, including through short-writing streams.

    ``write()`` should return how many bytes it took. Many file-like
    objects return ``None`` instead and take everything (a hand-written
    adapter, ``codecs.StreamWriter``); that is accepted. Only for a raw,
    unbuffered stream (``io.RawIOBase``) does ``None`` mean "nothing could
    be written yet" -- a non-blocking descriptor -- which cannot be told
    from a hang, so it is refused like any other impossible answer.
    """
    view = memoryview(chunk)
    while view:
        written = stream.write(view)
        if written is None and not isinstance(stream, io.RawIOBase):
            return
        if (isinstance(written, bool) or not isinstance(written, int)
                or written <= 0 or written > len(view)):
            raise InvalidArgumentError(
                "output stream write() must return a positive byte count "
                "(or None for a stream that always writes everything).",
                operation=operation,
            )
        view = view[written:]


# How long a finished call waits for a caller input stream's blocked read().
_INPUT_PUMP_JOIN_SECONDS = 1.0


def _start_stream_pumps(
    proc,
    parent_inputs,
    parent_outputs,
    operation: str,
):
    """Copy caller streams through worker pipes and report errors/counts.

    Also returns an Event that the caller sets once the worker is gone:
    an input pump then starts no further read() on the caller's stream.
    A read already blocked cannot be interrupted and still finishes in its
    daemon thread; whatever it returns is discarded.
    """
    threads = []
    errors = []
    counts = {}
    error_lock = threading.Lock()
    stop = threading.Event()

    def fail(exc) -> None:
        with error_lock:
            first = not errors
            if first:
                errors.append(exc)
        # Stop the worker outside the lock: it can wait up to the stop grace
        # period, and other pumps must be able to record their own failure.
        if first:
            _stop_worker(proc)

    def pump_input(fd: int, stream) -> None:
        try:
            with os.fdopen(fd, "wb", buffering=0) as destination:
                while not stop.is_set():
                    chunk = stream.read(128 * 1024)
                    if stop.is_set():
                        break
                    if not isinstance(chunk, (bytes, bytearray, memoryview)):
                        raise InvalidArgumentError(
                            "input stream read() must return bytes.",
                            operation=operation,
                        )
                    if not chunk:
                        break
                    _write_all(destination.fileno(), bytes(chunk))
        except BrokenPipeError:
            # The worker may intentionally stop after a bounded read, or may
            # reject an operation before consuming the whole input.
            pass
        except BaseException as exc:
            fail(exc)

    def pump_output(name: str, fd: int, stream) -> None:
        total = 0
        try:
            with os.fdopen(fd, "rb", buffering=0) as source:
                while True:
                    chunk = source.read(128 * 1024)
                    if not chunk:
                        break
                    _write_stream_chunk(stream, chunk, operation)
                    total += len(chunk)
            counts[name] = total
        except BaseException as exc:
            fail(exc)

    for fd, stream in parent_inputs:
        thread = threading.Thread(
            target=pump_input, args=(fd, stream), daemon=True
        )
        thread._pd_input_pump = True
        thread.start()
        threads.append(thread)
    for name, fd, stream in parent_outputs:
        thread = threading.Thread(
            target=pump_output, args=(name, fd, stream), daemon=True
        )
        thread.start()
        threads.append(thread)
    return threads, errors, counts, stop


def _run_worker(operation: str, options: dict,
                config: Optional[RuntimeConfig] = None,
                event_handler: Optional[EventHandler] = None,
                credential_provider: Optional[CredentialProvider] = None,
                input_streams: Optional[Mapping[str, BinaryIO]] = None,
                output_streams: Optional[Mapping[str, BinaryIO]] = None,
                cancel_event=None, reported_operation=None) -> dict:
    """Run one stateful operation in a child and return its JSON result."""
    api_operation = reported_operation or operation
    cancel_event = _cancel_control(cancel_event, operation=api_operation)
    config = config or RuntimeConfig.default()
    options = dict(options)
    # The worker reads at most _MAX_WORKER_REQUEST_BYTES. A larger request
    # (thousands of build arguments, say) is the caller's argument, and is
    # refused here instead of as a generic failure from inside the worker.
    # The descriptor table added below is a few dozen bytes; leave room.
    if len(json.dumps(
        {"operation": operation, "options": options},
        ensure_ascii=True, separators=(",", ":"),
    )) > _MAX_WORKER_REQUEST_BYTES - 4096:
        raise InvalidArgumentError(
            f"{api_operation} arguments are too large.",
            operation=api_operation,
        )
    child_stream_fds = {}
    parent_inputs = []
    parent_outputs = []
    child_pipe_fds = []
    event_read = event_write = None
    build_output_read = build_output_write = None
    try:
        for name, stream in (input_streams or {}).items():
            child_fd, parent_fd = os.pipe()
            child_stream_fds[name] = child_fd
            child_pipe_fds.append(child_fd)
            parent_inputs.append((parent_fd, stream))
        for name, stream in (output_streams or {}).items():
            parent_fd, child_fd = os.pipe()
            child_stream_fds[name] = child_fd
            child_pipe_fds.append(child_fd)
            parent_outputs.append((name, parent_fd, stream))
        event_read, event_write = os.pipe()
        child_pipe_fds.append(event_write)
        if operation == "build":
            build_output_read, build_output_write = os.pipe()
            child_pipe_fds.append(build_output_write)
    except OSError as exc:
        for fd in child_pipe_fds:
            os.close(fd)
        for fd, _stream in parent_inputs:
            os.close(fd)
        for _name, fd, _stream in parent_outputs:
            os.close(fd)
        for fd in (event_read, build_output_read):
            if fd is not None:
                os.close(fd)
        raise OperationError(
            f"cannot create the {api_operation} stream channel: {exc}",
            operation=api_operation,
        ) from exc
    if child_stream_fds:
        options["_stream_fds"] = child_stream_fds
    request = json.dumps(
        {"operation": operation, "options": options},
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("ascii")
    parent_credentials = child_credentials = None
    pass_fds = tuple(child_pipe_fds)
    env = _worker_environment(config)
    env["_PROOT_DISTRO_EVENT_FD"] = str(event_write)
    if build_output_write is not None:
        env["_PROOT_DISTRO_BUILD_OUTPUT_FD"] = str(build_output_write)
    if credential_provider is not None:
        try:
            parent_credentials, child_credentials = socket.socketpair()
        except OSError as exc:
            for fd in child_pipe_fds:
                os.close(fd)
            for fd, _stream in parent_inputs:
                os.close(fd)
            for _name, fd, _stream in parent_outputs:
                os.close(fd)
            os.close(event_read)
            if build_output_read is not None:
                os.close(build_output_read)
            raise OperationError(
                f"cannot create credential provider channel: {exc}",
                operation=api_operation,
            ) from exc
        env["_PROOT_DISTRO_CREDENTIAL_FD"] = str(child_credentials.fileno())
        pass_fds += (child_credentials.fileno(),)
    try:
        proc = subprocess.Popen(
            private_module_command("proot_distro._api_worker"),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            pass_fds=pass_fds,
            start_new_session=True,
        )
    except (OSError, ValueError) as exc:
        if parent_credentials is not None:
            parent_credentials.close()
            child_credentials.close()
        for fd in child_pipe_fds:
            os.close(fd)
        for fd, _stream in parent_inputs:
            os.close(fd)
        for _name, fd, _stream in parent_outputs:
            os.close(fd)
        os.close(event_read)
        if build_output_read is not None:
            os.close(build_output_read)
        raise OperationError(
            f"cannot start the {api_operation} worker: {exc}",
            operation=api_operation,
        ) from exc

    if child_credentials is not None:
        child_credentials.close()
    for fd in child_pipe_fds:
        os.close(fd)
    pump_threads, stream_errors, stream_counts, pumps_stop = (
        _start_stream_pumps(
            proc, parent_inputs, parent_outputs, api_operation
        )
    )
    build_output_resource = None
    if operation == "build":
        tags = options.get("tags")
        if isinstance(tags, list) and tags:
            build_output_resource = tags[0]
        else:
            build_output_resource = (
                options.get("install_as") or options.get("path")
            )
    try:
        stdout, stderr = _communicate_with_channels(
            proc,
            request,
            event_read,
            event_handler,
            api_operation,
            credential_sock=parent_credentials,
            credential_provider=credential_provider,
            build_output_fd=build_output_read,
            build_output_resource=build_output_resource,
            cancel_event=cancel_event,
        )
    except KeyboardInterrupt:
        raise
    except BaseException:
        _stop_worker(proc)
        proc.wait()
        raise
    finally:
        # The worker is gone: no input pump may start another read().
        pumps_stop.set()
        for thread in pump_threads:
            if getattr(thread, "_pd_input_pump", False):
                # The worker has exited. An input pump still blocked in the
                # caller's read() (a socket or pipe that never reaches EOF)
                # cannot be interrupted, so give it a moment and let the
                # daemon thread end on its own instead of hanging the call.
                thread.join(_INPUT_PUMP_JOIN_SECONDS)
            else:
                thread.join()

    if stream_errors:
        raise stream_errors[0]

    if len(stdout) > _MAX_WORKER_RESULT_BYTES:
        raise OperationError(
            f"{api_operation} worker returned an oversized result.",
            operation=api_operation,
        )
    if proc.returncode != 0:
        structured = _structured_worker_error(api_operation, stdout)
        if structured is not None:
            raise structured
        raise _worker_error(api_operation, stderr)
    try:
        result = json.loads(stdout)
    except (ValueError, UnicodeDecodeError, RecursionError) as exc:
        raise OperationError(
            f"{api_operation} worker returned an invalid result.",
            operation=api_operation,
        ) from exc
    if not isinstance(result, dict):
        raise OperationError(
            f"{api_operation} worker returned an invalid result.",
            operation=api_operation,
        )
    raw_events = result.pop("_events", [])
    if not isinstance(raw_events, list) or raw_events:
        raise OperationError(
            f"{api_operation} worker returned invalid events.",
            operation=api_operation,
        )
    if stream_counts:
        result["_stream_bytes"] = stream_counts
    return result


@dataclass(frozen=True)
class _OperationContext:
    """Dependencies shared by operations of one public facade instance."""

    config: RuntimeConfig
    event_handler: Optional[EventHandler]
    credentials: Optional[RegistryCredentials]
    credential_provider: Optional[CredentialProvider]

    def event(
        self, operation: str, kind: EventKind, message: str, *,
        phase: Optional[str] = None, resource: Optional[str] = None,
    ) -> None:
        """Hand the caller an event this process produced, not a worker.

        The message is escaped (printable_message) as a worker's are: a
        search's retry notice quotes the HTTP error, reason phrase and
        all, and search runs here rather than in a worker.
        """
        if self.event_handler is not None:
            self.event_handler(OperationEvent(
                operation, kind, printable_message(message), phase=phase,
                resource=resource,
            ))

    def worker(
        self, operation: str, options: dict, *,
        credential_provider: Optional[CredentialProvider] = None,
        input_streams: Optional[Mapping[str, BinaryIO]] = None,
        output_streams: Optional[Mapping[str, BinaryIO]] = None,
        cancel_event=None,
        reported_operation=None,
    ) -> dict:
        return _run_worker(
            operation, options, self.config, self.event_handler,
            credential_provider, input_streams, output_streams, cancel_event,
            reported_operation,
        )

    def registry_credentials(
        self, operation: str, *, target: Optional[str] = None,
        override: Optional[RegistryCredentials] = None,
    ) -> Optional[CredentialProvider]:
        """Return what answers a worker's per-registry credential requests.

        The worker asks by registry name at the moment it authenticates,
        and _handle_credential_request drops anything scoped to another
        registry. *target* is the registry_key the operation's own argument
        names (install, push), or None when the registries are chosen by a
        Dockerfile or by the container's guest-writable manifest (build,
        reset). Unscoped static credentials are refused there: nothing the
        caller said ties them to a registry.
        """
        static = override if override is not None else self.credentials
        if static is None:
            return self.credential_provider
        scope = static.registry
        if scope is None:
            if target is None:
                raise InvalidArgumentError(
                    f"{operation} can contact registries its arguments do "
                    "not name; give RegistryCredentials a registry= or use "
                    "a credential_provider.",
                    operation=operation,
                )
            scope = target

        def answer(request: CredentialRequest):
            return static if request.registry == scope else None

        return answer


class ProotDistro:
    """Standardized programmatic access to PRoot-Distro operations.

    The facade does not connect to a service and starts no persistent
    background process. Stateful methods use a short-lived child so each
    instance can apply its own runtime configuration without changing the
    embedding process's environment or imported module constants.

    Args:
        config: Runtime and cache locations. ``None`` uses the same storage
            as the command-line utility.
        event_handler: Optional callable receiving ``OperationEvent``
            records (information, warnings, progress, build output and
            network retries). It runs synchronously in the calling thread,
            so it applies backpressure; exceptions it raises stop the
            operation and propagate to the caller.
        credentials: Static registry credentials. With ``registry=`` they
            are sent only to that registry. Without it, only to the
            registry an ``install`` or ``push`` argument names; ``build``
            and ``reset_container`` refuse them. Mutually exclusive with
            ``credential_provider``.
        credential_provider: Callable that chooses credentials per registry
            request, asked when an operation is about to authenticate. It
            runs synchronously in the calling thread.

    Every method that starts work accepts ``cancel_event`` (a token with an
    ``is_set()`` method) except ``search``. All failures are subclasses of
    ``ProotDistroError``; see ``proot_distro.api.errors``. User documentation
    is in ``API.md``.
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

    def _event(
        self, operation: str, kind: EventKind, message: str, *,
        phase: Optional[str] = None, resource: Optional[str] = None,
    ) -> None:
        self._context.event(
            operation, kind, message, phase=phase, resource=resource,
        )

    def list_containers(
        self, *, cancel_event: Optional[CancellationToken] = None,
    ) -> Tuple[ContainerInfo, ...]:
        """Return installed containers sorted by name."""
        result = self._context.worker(
            "list_containers", {}, cancel_event=cancel_event,
        )
        items = result.get("items")
        try:
            if not isinstance(items, list):
                raise TypeError
            return tuple(_container_info(item) for item in items)
        except (TypeError, ValueError):
            raise OperationError(
                "container inventory worker returned an invalid result.",
                operation="list_containers",
            ) from None

    def list_images(
        self, *, cancel_event: Optional[CancellationToken] = None,
    ) -> Tuple[ImageInfo, ...]:
        """Return locally cached OCI images in deterministic order."""
        result = self._context.worker(
            "list_images", {}, cancel_event=cancel_event
        )
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

    def list_sessions(
        self, *, cancel_event: Optional[CancellationToken] = None,
    ) -> Tuple[SessionInfo, ...]:
        """Return validated records for every live PRoot-Distro session."""
        result = self._context.worker(
            "list_sessions", {}, cancel_event=cancel_event
        )
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


    def install(
        self,
        source: _PathArgument,
        *,
        name: Optional[str] = None,
        architecture: Optional[str] = None,
        allow_insecure: bool = False,
        cancel_event: Optional[CancellationToken] = None,
    ) -> ContainerInfo:
        """Install a container from an image reference, URL, or archive.

        Args:
            source: Image reference, ``http(s)`` URL or local archive path.
            name: Container name; derived from the source when omitted.
            architecture: Target CPU architecture for registry images.
            allow_insecure: Permit plain-HTTP registries.
            cancel_event: Optional cancellation token.

        Raises:
            InvalidArgumentError: An argument is malformed.
            AlreadyExistsError: A container with that name exists, the
                name holds the leftovers of an incomplete container, or
                something took it while the container was being built.
            NotFoundError: The image, URL or archive does not exist.
            AuthenticationError: The registry refused the credentials.
            NetworkError: The registry or URL could not be reached.
            IntegrityError: Downloaded or archived content failed validation.
            BusyError: The container name is locked by another operation.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(cancel_event, operation="install")
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

        # Only a registry reference contacts a registry; a local archive or
        # a URL never gets credentials, scoped or not.
        provider = None
        if reference is not None:
            provider = self._context.registry_credentials(
                "install", target=registry_key(reference.registry),
            )

        result = self._context.worker("install", {
            "source": source,
            "name": name,
            "architecture": architecture,
            "allow_insecure": allow_insecure,
        }, credential_provider=provider, cancel_event=cancel_event)
        return _reported_container(result, "install", name)

    def remove_container(
        self, name: str, *,
        cancel_event: Optional[CancellationToken] = None,
    ) -> ContainerInfo:
        """Permanently remove a container and return its former metadata.

        Raises:
            InvalidArgumentError: *name* is malformed.
            NotFoundError: The container does not exist.
            BusyError: The container is locked by another operation or session.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(
            cancel_event, operation="remove_container"
        )
        name = _require_name(name, operation="remove_container")
        result = self._context.worker(
            "remove_container", {"name": name}, cancel_event=cancel_event
        )
        return _reported_container(result, "remove_container", name)

    def rename_container(
        self, old_name: str, new_name: str, *,
        cancel_event: Optional[CancellationToken] = None,
    ) -> ContainerInfo:
        """Rename a container and return its metadata under the new name.

        Raises:
            InvalidArgumentError: A name is malformed or both are equal.
            NotFoundError: The container does not exist.
            AlreadyExistsError: The new name is taken.
            BusyError: The container is locked by another operation or session.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(
            cancel_event, operation="rename_container"
        )
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
        result = self._context.worker("rename_container", {
            "old_name": old_name,
            "new_name": new_name,
        }, cancel_event=cancel_event)
        return _reported_container(result, "rename_container", new_name)

    def reset_container(
        self, name: str, *,
        cancel_event: Optional[CancellationToken] = None,
    ) -> ContainerInfo:
        """Reinstall a container from its recorded OCI image.

        All changes made inside the container are lost. The image reference
        is read from the container's own metadata, so static credentials
        must be scoped with ``registry=``.

        Raises:
            InvalidArgumentError: *name* is malformed, or the static
                credentials have no ``registry``.
            NotFoundError: The container does not exist.
            UnsupportedOperationError: It was not installed from an OCI image.
            BusyError: The container is locked by another operation or session.
            AlreadyExistsError: Something took the name while the new
                container was being built.
            AuthenticationError: The registry refused the credentials.
            NetworkError: The registry could not be reached.
            IntegrityError: Downloaded content failed validation.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(
            cancel_event, operation="reset_container"
        )
        name = _require_name(name, operation="reset_container")
        # The registry comes from the container's own manifest, so it is
        # asked for while the worker resets, by the name it contacts.
        provider = self._context.registry_credentials("reset_container")
        result = self._context.worker("reset_container", {
            "name": name,
        }, credential_provider=provider, cancel_event=cancel_event)
        return _reported_container(result, "reset_container", name)

    def _transfer(
        self,
        operation: str,
        source,
        destination,
        cancel_event=None,
        **options,
    ) -> TransferResult:
        cancel_event = _cancel_control(cancel_event, operation=operation)
        source = _path_argument(
            source, operation=operation, argument="source"
        )
        destination = _path_argument(
            destination, operation=operation, argument="destination"
        )
        source_container = container_from_spec(source)
        destination_container = container_from_spec(destination)
        # A spec's container name is checked here, so a typo such as
        # ":/etc" or "~:x" is an argument error. Whether it is installed is
        # the worker's to say (NotFoundError), under the locks it takes.
        for role, container in (
            ("source", source_container),
            ("destination", destination_container),
        ):
            if container is not None:
                _require_name(
                    container, operation=operation,
                    argument=f"{role} container",
                )
        # A host path is made absolute and "~"-expanded here, whatever its
        # spelling. The worker and the existence check below must see the
        # same path: expanding only for the check let "~/file" pass it and
        # then be handed to the worker as a literal relative "~" directory.
        if source_container is None:
            source = _host_path(source)
        if destination_container is None:
            destination = _host_path(destination)
        if source_container is None:
            exists = (os.path.lexists(source) if operation == "move"
                      else os.path.exists(source))
            if not exists:
                raise NotFoundError(
                    f"source path '{source}' does not exist.",
                    operation=operation,
                )
        result = self._context.worker(operation, {
            "source": source,
            "destination": destination,
            **options,
        }, cancel_event=cancel_event)
        if (result.get("source") != source
                or result.get("destination") != destination):
            raise OperationError(
                f"{operation} worker returned an invalid result.",
                operation=operation,
            )
        return TransferResult(operation, source, destination)

    def copy(
        self, source: _PathArgument, destination: _PathArgument, *,
        recursive: bool = False,
        cancel_event: Optional[CancellationToken] = None,
    ) -> TransferResult:
        """Copy a file or directory between host and container paths.

        Paths use ``container:path`` syntax. A ``str`` host path is made
        absolute (``~`` is expanded) and a ``pathlib.Path`` always means a
        host path.

        Raises:
            InvalidArgumentError: A path is malformed.
            NotFoundError: A container or the source is missing.
            BusyError: The container is locked by another operation or session.
            PartialOperationError: Some entries could not be copied.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        if not isinstance(recursive, bool):
            raise InvalidArgumentError(
                "recursive must be a boolean.", operation="copy"
            )
        return self._transfer(
            "copy", source, destination, cancel_event=cancel_event,
            move=False, recursive=recursive,
        )

    def move(
        self, source: _PathArgument, destination: _PathArgument, *,
        cancel_event: Optional[CancellationToken] = None,
    ) -> TransferResult:
        """Move a file or directory between host and container paths.

        Same path rules as :meth:`copy`.

        Raises:
            InvalidArgumentError: A path is malformed.
            NotFoundError: A container or the source is missing.
            BusyError: The container is locked by another operation or session.
            PartialOperationError: Some entries could not be moved.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        return self._transfer(
            "move", source, destination, cancel_event=cancel_event,
            move=True, recursive=False,
        )

    def sync(
        self,
        source: _PathArgument,
        destination: _PathArgument,
        *,
        checksum: bool = False,
        delete: bool = False,
        cancel_event: Optional[CancellationToken] = None,
    ) -> TransferResult:
        """Synchronize a file or directory to another path.

        Same path rules as :meth:`copy`. With ``delete=True`` entries that
        exist only in the destination are removed.

        Raises:
            InvalidArgumentError: A path or flag is invalid.
            NotFoundError: A container or the source is missing.
            BusyError: The container is locked by another operation or session.
            PartialOperationError: Some entries could not be synchronized.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        if not isinstance(checksum, bool):
            raise InvalidArgumentError(
                "checksum must be a boolean.", operation="sync"
            )
        if not isinstance(delete, bool):
            raise InvalidArgumentError(
                "delete must be a boolean.", operation="sync"
            )
        return self._transfer(
            "sync", source, destination, cancel_event=cancel_event,
            checksum=checksum, delete=delete,
        )

    def backup(
        self,
        name: str,
        output: _PathOrBinaryStream,
        *,
        compression: Optional[str] = None,
        cancel_event: Optional[CancellationToken] = None,
    ) -> BackupResult:
        """Write a container backup to a host path or binary stream.

        Args:
            name: Container to archive.
            output: New host path (compression is chosen from its suffix) or
                a writable binary stream (no compression unless
                ``compression`` is given). The API never closes the stream.
            compression: ``None``, ``"none"``, ``"gzip"``, ``"bzip2"``,
                ``"xz"`` or ``"zstd"``.
            cancel_event: Optional cancellation token.

        Raises:
            InvalidArgumentError: An argument is malformed.
            NotFoundError: The container does not exist.
            AlreadyExistsError: The output path already exists.
            UnsupportedOperationError: The compression is unavailable, for example zstd needs Python 3.14.
            BusyError: The container is locked by another operation or session.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(cancel_event, operation="backup")
        name = _require_name(name, operation="backup")
        output_path, output_stream = _path_or_stream(
            output, operation="backup", argument="output", writable=True
        )
        allowed = (None, "gzip", "bzip2", "xz", "zstd", "none")
        if compression not in allowed:
            raise InvalidArgumentError(
                "compression must be gzip, bzip2, xz, zstd, none, or None.",
                operation="backup",
            )
        result = self._context.worker("backup", {
            "name": name,
            "output": output_path,
            "compression": compression,
        }, output_streams=(
            {"backup_output": output_stream} if output_stream is not None
            else None
        ), cancel_event=cancel_event)
        try:
            result_name = result["container_name"]
            result_output = result["output"]
            result_compression = result["compression"]
            if output_stream is None:
                size_bytes = result["size_bytes"]
            else:
                size_bytes = result["_stream_bytes"]["backup_output"]
            if (result_name != name or result_output != output_path
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
            name, output_path, result_compression, size_bytes
        )

    def restore(
        self, archive: _PathOrBinaryStream, *,
        cancel_event: Optional[CancellationToken] = None,
    ) -> RestoreResult:
        """Restore one container from a host path or binary stream.

        An existing container of the same name is replaced in place, which
        cannot be rolled back: if the restore fails or is cancelled, restore
        the archive again. A container created by this restore is removed
        again on failure.

        Raises:
            InvalidArgumentError: *archive* is not a path or readable stream.
            NotFoundError: The archive path does not exist.
            IntegrityError: The archive is damaged or malformed.
            AlreadyExistsError: The archive's container name holds the
                leftovers of an incomplete container, or something took
                it while the container was being built.
            UnsupportedOperationError: The archive's compression is unavailable, for example zstd needs Python 3.14.
            BusyError: The target container is locked by another operation or session.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(cancel_event, operation="restore")
        archive_path, archive_stream = _path_or_stream(
            archive, operation="restore", argument="archive", readable=True
        )
        result = self._context.worker(
            "restore", {"archive": archive_path},
            input_streams=(
                {"restore_input": archive_stream}
                if archive_stream is not None else None
            ),
            cancel_event=cancel_event,
        )
        if result.get("archive") != archive_path:
            raise OperationError(
                "restore worker returned an invalid archive path.",
                operation="restore",
            )
        return RestoreResult(
            _reported_container(result, "restore"), archive_path,
        )

    def build(
        self,
        path: _PathArgument,
        *,
        dockerfile: Optional[_PathOrBinaryStream] = None,
        tags: Iterable[str] = (),
        build_args: Optional[Mapping[str, str]] = None,
        architecture: Optional[str] = None,
        target: Optional[str] = None,
        emulator: Optional[_PathArgument] = None,
        outputs: _BuildOutputs = (),
        install_as: Optional[str] = None,
        no_cache: bool = False,
        credentials: Optional[RegistryCredentials] = None,
        cancel_event: Optional[CancellationToken] = None,
    ) -> BuildResult:
        """Build an OCI image from a Dockerfile and context directory.

        Args:
            path: Build context directory.
            dockerfile: Path or readable binary stream; default is the
                Dockerfile in the context.
            tags: Image tags to create.
            build_args: Mapping of build-argument names to string values.
                Do not pass secrets: they are visible to ``RUN`` steps.
            architecture, target, emulator: Build platform, stage, emulator.
            outputs: OCI archive paths or writable binary streams.
            install_as: Also install the result as this container name.
            no_cache: Ignore the Dockerfile build cache.
            credentials: Static registry credentials for this build,
                overriding the instance's credentials and provider. A
                Dockerfile chooses its registries, so they must name their
                ``registry``.
            cancel_event: Optional cancellation token.

        Raises:
            InvalidArgumentError: An argument is malformed, or the static
                credentials have no ``registry``.
            NotFoundError: The context or a referenced base is missing.
            AlreadyExistsError: The ``install_as`` container already exists.
            AuthenticationError: A registry refused the credentials.
            NetworkError: A registry could not be reached.
            BusyError: The image is locked by another operation.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(cancel_event, operation="build")
        path = _path_argument(
            path, operation="build", argument="path", host_only=True
        )
        dockerfile_stream = None
        if dockerfile is not None:
            dockerfile, dockerfile_stream = _path_or_stream(
                dockerfile, operation="build", argument="dockerfile",
                readable=True,
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
        encoded_build_args = {}
        for key, value in build_args.items():
            # Same rules as an environment entry, which is what a build
            # argument becomes in a RUN step: a NUL or an unencodable code
            # point used to fail only inside the worker, as a generic
            # error, and NAME=VALUE has no place for '=' in the name.
            key = _string_argument(
                key, operation="build", argument="build_args key"
            )
            if "=" in key:
                raise InvalidArgumentError(
                    "build_args keys must not contain '='.", operation="build"
                )
            encoded_build_args[key] = _string_argument(
                value, operation="build", argument="build_args value",
                allow_empty=True,
            )

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
        target = _string_argument(
            target, operation="build", argument="target", allow_none=True,
        )
        if emulator is not None:
            emulator = _path_argument(
                emulator, operation="build", argument="emulator",
                host_only=True,
            )
        if (isinstance(outputs, (str, os.PathLike))
                or callable(getattr(outputs, "write", None))):
            outputs = (outputs,)
        try:
            raw_outputs = tuple(outputs)
        except TypeError:
            raise InvalidArgumentError(
                "outputs must be an iterable of host paths or binary streams.",
                operation="build",
            ) from None
        output_paths = []
        build_output_streams = {}
        for index, output in enumerate(raw_outputs):
            output_path, output_stream = _path_or_stream(
                output, operation="build", argument="output", writable=True
            )
            output_paths.append(output_path)
            if output_stream is not None:
                build_output_streams[f"build_output_{index}"] = output_stream
        outputs = tuple(output_paths)
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
        build_provider = self._context.registry_credentials(
            "build", override=credentials,
        )

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
        }, credential_provider=build_provider,
            input_streams=(
                {"dockerfile_input": dockerfile_stream}
                if dockerfile_stream is not None else None
            ),
            output_streams=build_output_streams or None,
            cancel_event=cancel_event)
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
            installed = _reported_container(
                {"container": result.get("installed_container")}, "build",
                install_as,
            )
        return BuildResult(images, outputs, installed)

    def push(
        self,
        image_ref: str,
        *,
        architecture: Optional[str] = None,
        allow_insecure: bool = False,
        cancel_event: Optional[CancellationToken] = None,
    ) -> PushResult:
        """Push one cached image tag to its registry.

        Raises:
            InvalidArgumentError: An argument is malformed.
            NotFoundError: The image is not cached.
            AuthenticationError: The registry refused the credentials.
            NetworkError: The registry could not be reached.
            BusyError: The image is locked by another operation.
            IntegrityError: The cached image is damaged, or the registry
                answered the upload with a digest that is not the
                manifest's.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(cancel_event, operation="push")
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
        provider = self._context.registry_credentials(
            "push", target=registry_key(reference.registry),
        )
        result = self._context.worker("push", {
            "image_ref": image_ref,
            "architecture": architecture,
            "allow_insecure": allow_insecure,
        }, credential_provider=provider, cancel_event=cancel_event)
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
        cancel_event: Optional[CancellationToken] = None,
    ) -> ImageRemovalResult:
        """Remove cached image variants addressed by reference or image ID.

        Raises:
            InvalidArgumentError: An argument is malformed.
            NotFoundError: No cached image matches.
            BusyError: The image is locked by another operation.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(
            cancel_event, operation="remove_image"
        )
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
        }, cancel_event=cancel_event)
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
        cancel_event: Optional[CancellationToken] = None,
    ) -> CacheCleanupResult:
        """Clear all cache data or selected orphan/build-cache data.

        With no flags the whole cache is cleared. ``orphan`` limits it to
        layers no image uses; ``build_cache`` to the Dockerfile build cache.
        Whatever the flags, the scratch files that killed builds left are
        removed too, and so is what killed installs and restores left.
        They are not part of the cache, and the byte counts leave them
        out.

        Raises:
            InvalidArgumentError: A flag is not a boolean.
            BusyError: With ``orphan`` or ``build_cache``, a build or install
                is running; nothing was deleted.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(cancel_event, operation="clear_cache")
        if not isinstance(orphan, bool) or not isinstance(build_cache, bool):
            raise InvalidArgumentError(
                "orphan and build_cache must be booleans.",
                operation="clear_cache",
            )
        result = self._context.worker("clear_cache", {
            "orphan": orphan,
            "build_cache": build_cache,
        }, cancel_event=cancel_event)
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
        startup_timeout,
        cancel_event,
        **options,
    ) -> SessionProcess:
        operation = "start" if kind == "login" else "run"
        startup_timeout, cancel_event = _startup_controls(
            startup_timeout, cancel_event, operation=operation
        )
        container = _require_name(container, operation=operation)
        arguments = _string_list(
            arguments, operation=operation, argument="arguments"
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
                private_module_command("proot_distro._api_session_worker"),
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

        try:
            setup_error = _read_session_startup(
                status_read, operation, self._context.event_handler,
                startup_timeout, cancel_event,
            )
        except KeyboardInterrupt:
            _interrupt_starting_session(process)
            raise
        except BaseException:
            _stop_starting_session(process)
            raise
        finally:
            os.close(status_read)
        if setup_error is not None:
            # It reported and is exiting; one that does not is not waited
            # for without end.
            try:
                process.wait(timeout=_WORKER_STOP_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                _stop_starting_session(process)
            except KeyboardInterrupt:
                _stop_starting_session(process)
                raise
            raise _WORKER_ERROR_TYPES[
                getattr(setup_error, "kind", "operation")
            ](str(setup_error), operation=operation)
        return SessionProcess(
            self, process, container=container, kind=kind
        )

    def start(
        self,
        container: str,
        arguments: Iterable[str] = (),
        *,
        user: str = "root",
        environment: Optional[Mapping[str, str]] = None,
        binds: Iterable[str] = (),
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
        emulator: Optional[_PathArgument] = None,
        kernel: Optional[str] = None,
        hostname: Optional[str] = None,
        stdin: _SessionStdio = None,
        stdout: _SessionStdio = None,
        stderr: _SessionStdio = None,
        startup_timeout: Optional[float] = None,
        cancel_event: Optional[CancellationToken] = None,
    ) -> SessionProcess:
        """Start a login shell or shell-wrapped command inside a container.

        Without ``stdin``/``stdout``/``stderr`` the session shares this
        process's streams; other values follow ``subprocess.Popen``.
        ``startup_timeout`` (seconds, ``None`` for no limit) and
        ``cancel_event`` apply only until proot has been executed.

        Raises:
            InvalidArgumentError: An argument is malformed.
            NotFoundError: The container does not exist.
            BusyError: The container is locked by another operation or session.
            SessionStartupTimeoutError: Setup exceeded ``startup_timeout``.
            OperationCancelledError: Cancelled during setup.
            OperationError: Any other setup failure.
        """
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
            stderr=stderr, startup_timeout=startup_timeout,
            cancel_event=cancel_event, **options,
        )

    def run(
        self,
        container: str,
        arguments: Iterable[str] = (),
        *,
        user: str = "root",
        environment: Optional[Mapping[str, str]] = None,
        binds: Iterable[str] = (),
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
        emulator: Optional[_PathArgument] = None,
        kernel: Optional[str] = None,
        hostname: Optional[str] = None,
        stdin: _SessionStdio = None,
        stdout: _SessionStdio = None,
        stderr: _SessionStdio = None,
        startup_timeout: Optional[float] = None,
        cancel_event: Optional[CancellationToken] = None,
    ) -> SessionProcess:
        """Start the image Entrypoint/Cmd, optionally replacing Cmd arguments.

        Same options, streams and errors as :meth:`start`; the image's
        Entrypoint and Cmd define the command.

        Raises:
            InvalidArgumentError: An argument is malformed.
            NotFoundError: The container does not exist.
            BusyError: The container is locked by another operation or session.
            SessionStartupTimeoutError: Setup exceeded ``startup_timeout``.
            OperationCancelledError: Cancelled during setup.
            OperationError: Any other setup failure.
        """
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
            startup_timeout=startup_timeout, cancel_event=cancel_event,
            **options,
        )

    def _start_detached_session(
        self, kind: str, container: str, arguments, options: dict,
        cancel_event=None,
    ) -> DetachedSession:
        operation = "start_detached" if kind == "login" else "run_detached"
        cancel_event = _cancel_control(cancel_event, operation=operation)
        container = _require_name(container, operation=operation)
        arguments = _string_list(
            arguments, operation=operation, argument="arguments"
        )
        result = self._context.worker(operation, {
            "kind": kind,
            "container": container,
            "arguments": arguments,
            "options": options,
        }, cancel_event=cancel_event)
        pid = result.get("pid")
        if (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0
                or result.get("container") != container
                or result.get("kind") != kind):
            raise OperationError(
                "detached session worker returned an invalid result.",
                operation=operation,
            )
        return DetachedSession(pid, container, kind)

    def start_detached(
        self,
        container: str,
        arguments: Iterable[str] = (),
        *,
        user: str = "root",
        environment: Optional[Mapping[str, str]] = None,
        binds: Iterable[str] = (),
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
        emulator: Optional[_PathArgument] = None,
        kernel: Optional[str] = None,
        hostname: Optional[str] = None,
        stdout: _DetachedOutput = None,
        stderr: _DetachedOutput = None,
        cancel_event: Optional[CancellationToken] = None,
    ) -> DetachedSession:
        """Start a detached login command with optional append-only logs.

        The session outlives this process. Its input is ``/dev/null``;
        ``stdout`` and ``stderr`` are ``None``/``subprocess.DEVNULL``,
        append-only host paths, or (for ``stderr``) ``subprocess.STDOUT``.
        Returns after proot has started.

        Raises:
            InvalidArgumentError: An argument or log destination is invalid.
            NotFoundError: The container does not exist.
            BusyError: The container is locked by another operation or session.
            OperationCancelledError: Cancelled before the launch.
            OperationError: Any other failure.
        """
        stdout = _detached_output(
            stdout, operation="start_detached", argument="stdout"
        )
        stderr = _detached_output(
            stderr, operation="start_detached", argument="stderr",
            allow_stdout=True,
        )
        options = self._session_options(
            "start_detached", user=user, environment=environment, binds=binds,
            work_dir=work_dir, redirect_ports=redirect_ports,
            shared_home=shared_home, shared_tmp=shared_tmp,
            shared_x11=shared_x11, isolated=isolated, minimal=minimal,
            no_link2symlink=no_link2symlink, no_sysvipc=no_sysvipc,
            no_kill_on_exit=no_kill_on_exit, emulator=emulator,
            kernel=kernel, hostname=hostname,
        )
        options["detach_stdout"] = stdout
        options["detach_stderr"] = stderr
        return self._start_detached_session(
            "login", container, arguments, options,
            cancel_event=cancel_event,
        )

    def run_detached(
        self,
        container: str,
        arguments: Iterable[str] = (),
        *,
        user: str = "root",
        environment: Optional[Mapping[str, str]] = None,
        binds: Iterable[str] = (),
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
        emulator: Optional[_PathArgument] = None,
        kernel: Optional[str] = None,
        hostname: Optional[str] = None,
        stdout: _DetachedOutput = None,
        stderr: _DetachedOutput = None,
        cancel_event: Optional[CancellationToken] = None,
    ) -> DetachedSession:
        """Start the image Entrypoint/Cmd with optional append-only logs.

        Same options and errors as :meth:`start_detached`.

        Raises:
            InvalidArgumentError: An argument or log destination is invalid.
            NotFoundError: The container does not exist.
            BusyError: The container is locked by another operation or session.
            OperationCancelledError: Cancelled before the launch.
            OperationError: Any other failure.
        """
        stdout = _detached_output(
            stdout, operation="run_detached", argument="stdout"
        )
        stderr = _detached_output(
            stderr, operation="run_detached", argument="stderr",
            allow_stdout=True,
        )
        options = self._session_options(
            "run_detached", user=user, environment=environment, binds=binds,
            work_dir=work_dir, redirect_ports=redirect_ports,
            shared_home=shared_home, shared_tmp=shared_tmp,
            shared_x11=shared_x11, isolated=isolated, minimal=minimal,
            no_link2symlink=no_link2symlink, no_sysvipc=no_sysvipc,
            no_kill_on_exit=no_kill_on_exit, emulator=emulator,
            kernel=kernel, hostname=hostname,
        )
        options["detach_stdout"] = stdout
        options["detach_stderr"] = stderr
        return self._start_detached_session(
            "run", container, arguments, options,
            cancel_event=cancel_event,
        )

    def _inspect_session_command(
        self, kind: str, container: str, arguments, options: dict,
        cancel_event=None,
    ) -> SessionCommand:
        operation = "inspect_start" if kind == "login" else "inspect_run"
        cancel_event = _cancel_control(cancel_event, operation=operation)
        container = _require_name(container, operation=operation)
        arguments = _string_list(
            arguments, operation=operation, argument="arguments"
        )
        result = self._context.worker(operation, {
            "kind": kind,
            "container": container,
            "arguments": arguments,
            "options": options,
        }, cancel_event=cancel_event)
        try:
            executable = result["executable"]
            argv = tuple(result["argv"])
            environment = tuple(
                (entry[0], entry[1]) for entry in result["environment"]
            )
            inner_command = tuple(result["inner_command"])
            rootfs = result["rootfs"]
            work_dir = result["work_dir"]
            user = result["user"]
            shell_command = result["shell_command"]
            scalar_values = (
                executable, rootfs, work_dir, user, shell_command,
            )
            if (result.get("container") != container
                    or result.get("kind") != kind
                    or not all(isinstance(value, str) and value
                               for value in scalar_values)
                    or not argv or argv[0] != executable
                    or not all(isinstance(value, str) for value in argv)
                    or not all(isinstance(key, str) and isinstance(value, str)
                               for key, value in environment)
                    or not all(isinstance(value, str)
                               for value in inner_command)
                    or f"--rootfs={rootfs}" not in argv
                    or "--rootfs=." in argv):
                raise ValueError
        except (KeyError, TypeError, ValueError, IndexError):
            raise OperationError(
                "session inspection worker returned an invalid result.",
                operation=operation,
            ) from None
        return SessionCommand(
            kind=kind,
            container=container,
            executable=executable,
            argv=argv,
            environment=environment,
            inner_command=inner_command,
            rootfs=rootfs,
            work_dir=work_dir,
            user=user,
            shell_command=shell_command,
        )

    def inspect_start(
        self,
        container: str,
        arguments: Iterable[str] = (),
        *,
        user: str = "root",
        environment: Optional[Mapping[str, str]] = None,
        binds: Iterable[str] = (),
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
        emulator: Optional[_PathArgument] = None,
        kernel: Optional[str] = None,
        hostname: Optional[str] = None,
        cancel_event: Optional[CancellationToken] = None,
    ) -> SessionCommand:
        """Inspect the path-based command corresponding to :meth:`start`.

        Prepares the session without executing proot. See ``API.md`` for the
        difference between the printable command and a real session.

        Raises:
            InvalidArgumentError: An argument is malformed.
            NotFoundError: The container does not exist.
            BusyError: The container is locked by another operation or session.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        options = self._session_options(
            "inspect_start", user=user, environment=environment, binds=binds,
            work_dir=work_dir, redirect_ports=redirect_ports,
            shared_home=shared_home, shared_tmp=shared_tmp,
            shared_x11=shared_x11, isolated=isolated, minimal=minimal,
            no_link2symlink=no_link2symlink, no_sysvipc=no_sysvipc,
            no_kill_on_exit=no_kill_on_exit, emulator=emulator,
            kernel=kernel, hostname=hostname,
        )
        return self._inspect_session_command(
            "login", container, arguments, options,
            cancel_event=cancel_event,
        )

    def inspect_run(
        self,
        container: str,
        arguments: Iterable[str] = (),
        *,
        user: str = "root",
        environment: Optional[Mapping[str, str]] = None,
        binds: Iterable[str] = (),
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
        emulator: Optional[_PathArgument] = None,
        kernel: Optional[str] = None,
        hostname: Optional[str] = None,
        cancel_event: Optional[CancellationToken] = None,
    ) -> SessionCommand:
        """Inspect the path-based command corresponding to :meth:`run`.

        Same options and errors as :meth:`inspect_start`.

        Raises:
            InvalidArgumentError: An argument is malformed.
            NotFoundError: The container does not exist.
            BusyError: The container is locked by another operation or session.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        options = self._session_options(
            "inspect_run", user=user, environment=environment, binds=binds,
            work_dir=work_dir, redirect_ports=redirect_ports,
            shared_home=shared_home, shared_tmp=shared_tmp,
            shared_x11=shared_x11, isolated=isolated, minimal=minimal,
            no_link2symlink=no_link2symlink, no_sysvipc=no_sysvipc,
            no_kill_on_exit=no_kill_on_exit, emulator=emulator,
            kernel=kernel, hostname=hostname,
        )
        return self._inspect_session_command(
            "run", container, arguments, options,
            cancel_event=cancel_event,
        )

    def kill_session(
        self, target: Union[int, str], *,
        signal_name: _SignalArgument = "TERM",
        cancel_event: Optional[CancellationToken] = None,
    ) -> SessionSignalResult:
        """Signal a tracked session PID or every session of a container.

        *target* is a PID, or a container name to signal all its sessions.
        The whole tracked process tree is signalled, not only its parent.

        Raises:
            InvalidArgumentError: *target* or *signal_name* is invalid.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(
            cancel_event, operation="kill_session"
        )
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
        selected_signal = _signal_argument(
            signal_name, operation="kill_session"
        )
        result = self._context.worker("kill_session", {
            "target": target_text,
            "signal": selected_signal.name,
        }, cancel_event=cancel_event)
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

    def kill_all_sessions(
        self, *, signal_name: _SignalArgument = "TERM",
        cancel_event: Optional[CancellationToken] = None,
    ) -> SessionSignalResult:
        """Signal every active tracked session in this runtime.

        Raises:
            InvalidArgumentError: *signal_name* is not a valid signal.
            OperationCancelledError: Cancelled before completion.
            OperationError: Any other failure.
        """
        cancel_event = _cancel_control(
            cancel_event, operation="kill_all_sessions"
        )
        selected_signal = _signal_argument(
            signal_name, operation="kill_all_sessions"
        )
        result = self._context.worker("kill_session", {
            "target": None,
            "all": True,
            "signal": selected_signal.name,
        }, cancel_event=cancel_event, reported_operation="kill_all_sessions")
        pids = result.get("pids")
        if (not isinstance(pids, list)
                or any(isinstance(pid, bool) or not isinstance(pid, int)
                       or pid <= 0 for pid in pids)):
            raise OperationError(
                "kill_all_sessions worker returned an invalid result.",
                operation="kill_all_sessions",
            )
        return SessionSignalResult("*", selected_signal.name, tuple(pids))

    def search(
        self, query: str, *, limit: int = SEARCH_DEFAULT_LIMIT,
    ) -> SearchResult:
        """Search public Docker Hub repositories.

        Search runs in the calling process, without a worker, and cannot be
        cancelled. Registry credentials are never sent to Docker Hub's
        public search endpoint.

        Raises:
            InvalidArgumentError: *query* or *limit* is invalid.
            NetworkError: Docker Hub is unreachable or refused the request
                (including HTTP 429 and 5xx responses).
            OperationError: Docker Hub returned a malformed response.
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
            self._event(
                "search", EventKind.RETRY, message,
                phase="network", resource=normalized,
            )

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
            # The helper wraps HTTP refusals (429, 5xx) and certificate
            # failures in RuntimeError; their cause marks them as network
            # failures a caller may reasonably retry.
            if isinstance(exc.__cause__, NETWORK_ERRORS):
                raise NetworkError(str(exc), operation="search") from exc
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


__all__ = ("ProotDistro",)
