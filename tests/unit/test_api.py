import hashlib
import io
import inspect
import json
import os
import socket
import subprocess
import sys
import threading

import pytest

from proot_distro.api import (
    API_VERSION,
    AlreadyExistsError,
    AuthenticationError,
    BackupResult,
    BuildResult,
    BusyError,
    CacheCleanupResult,
    CancellationToken,
    ContainerInfo,
    CredentialProvider,
    CredentialRequest,
    DetachedSession,
    EventHandler,
    EventKind,
    ImageInfo,
    ImageRemovalResult,
    IntegrityError,
    InvalidArgumentError,
    NetworkError,
    NotFoundError,
    OperationCancelledError,
    OperationError,
    OperationEvent,
    ProotDistro,
    ProotDistroError,
    PartialOperationError,
    PushResult,
    RegistryCredentials,
    RestoreResult,
    RuntimeConfig,
    SearchHit,
    SearchResult,
    SessionInfo,
    SessionCommand,
    SessionProcess,
    SessionSignalResult,
    SessionStartupTimeoutError,
    TransferResult,
    UnsupportedOperationError,
)
from proot_distro.api import program
from proot_distro.api import _session_worker, _worker
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import canonical_json


_DEEPLY_NESTED_JSON = b"[" * 2000 + b"]" * 2000


def test_public_exports_are_explicit():
    import proot_distro.api as api

    assert set(api.__all__) == {
        "API_VERSION", "AlreadyExistsError", "AuthenticationError", "BackupResult",
        "BuildResult", "BusyError", "CacheCleanupResult", "ContainerInfo",
        "CancellationToken", "CredentialProvider",
        "CredentialRequest", "DetachedSession", "EventHandler", "EventKind",
        "ImageInfo",
        "ImageRemovalResult",
        "IntegrityError", "InvalidArgumentError", "NetworkError",
        "NotFoundError", "OperationError", "OperationEvent",
        "OperationCancelledError", "PartialOperationError", "ProotDistro",
        "ProotDistroError", "PushResult",
        "RegistryCredentials", "RestoreResult", "RuntimeConfig", "SearchHit",
        "SearchResult", "SessionCommand", "SessionInfo", "SessionProcess",
        "SessionSignalResult", "SessionStartupTimeoutError", "TransferResult",
        "UnsupportedOperationError",
    }


def test_api_version_is_independent_string_metadata():
    assert API_VERSION == "1.0"


def test_public_protocols_accept_documented_structures():
    assert isinstance(lambda _event: None, EventHandler)
    assert isinstance(lambda _request: None, CredentialProvider)
    assert isinstance(threading.Event(), CancellationToken)


def test_public_models_copy_mutable_collections():
    command = ["/bin/sh"]
    hits = [SearchHit("alpine", "small", 1, 2, True, False)]
    image = ImageInfo(
        "alpine:latest", "alpine:latest", "library/alpine", "x86_64",
        None, 1, 1, 0, None, 1.0,
    )
    images = [image]
    outputs = [None]
    removed = [image]
    pids = [123]
    argv = ["/usr/bin/proot"]
    environment = [["HOME", "/root"]]
    inner_command = ["/bin/sh"]
    session = SessionInfo(
        123, "box", "login", command, "root", 1.0, False, False, False
    )
    result = SearchResult("alpine", 1, hits)
    build = BuildResult(images, outputs, None)
    removal = ImageRemovalResult(removed)
    signal_result = SessionSignalResult("123", "SIGTERM", pids)
    inspected = SessionCommand(
        "login", "box", "/usr/bin/proot", argv, environment,
        inner_command, "/rootfs", "/root", "root", '"/usr/bin/proot"',
    )

    command.append("changed")
    hits.clear()
    images.clear()
    outputs.clear()
    removed.clear()
    pids.clear()
    argv.append("changed")
    environment[0][1] = "changed"
    inner_command.append("changed")

    assert session.command == ("/bin/sh",)
    assert result.hits == (SearchHit("alpine", "small", 1, 2, True, False),)
    assert build.images == (image,)
    assert build.outputs == (None,)
    assert removal.removed == (image,)
    assert signal_result.pids == (123,)
    assert inspected.argv == ("/usr/bin/proot",)
    assert inspected.environment == (("HOME", "/root"),)
    assert inspected.inner_command == ("/bin/sh",)


def test_import_does_not_change_process_umask():
    code = """
import os
os.umask(0o077)
import proot_distro.api
observed = os.umask(0o077)
raise SystemExit(0 if observed == 0o077 else 1)
"""
    result = subprocess.run([sys.executable, "-c", code], check=False)
    assert result.returncode == 0


def test_constructor_rejects_non_callable_event_handler():
    with pytest.raises(InvalidArgumentError) as exc:
        ProotDistro(event_handler="print")
    assert exc.value.operation == "initialize"


def test_constructor_rejects_unknown_config_type():
    with pytest.raises(InvalidArgumentError) as exc:
        ProotDistro(config={})
    assert exc.value.operation == "initialize"


def test_registry_credentials_hide_password_from_repr():
    credentials = RegistryCredentials("alice", "very-secret")
    assert credentials.username == "alice"
    assert credentials.password == "very-secret"
    assert "very-secret" not in repr(credentials)


@pytest.mark.parametrize("username,password", [
    ("", "secret"),
    ("user:name", "secret"),
    ("alice", ""),
    ("alice\0", "secret"),
    ("alice", "secret\0"),
])
def test_registry_credentials_validate_fields(username, password):
    with pytest.raises(InvalidArgumentError) as exc:
        RegistryCredentials(username, password)
    assert exc.value.operation == "configure"


@pytest.mark.parametrize("kwargs", [
    {"credentials": "alice:secret"},
    {"credential_provider": "provider"},
    {
        "credentials": RegistryCredentials("alice", "secret"),
        "credential_provider": lambda _request: None,
    },
])
def test_constructor_validates_credential_configuration(kwargs):
    with pytest.raises(InvalidArgumentError) as exc:
        ProotDistro(**kwargs)
    assert exc.value.operation == "initialize"


def test_runtime_config_normalizes_paths(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    config = RuntimeConfig("state", "cache", "bin/proot")

    assert config.runtime_dir == os.path.join(str(tmp_path), "state")
    assert config.cache_dir == os.path.join(str(tmp_path), "cache")
    assert config.proot_path == os.path.join(str(tmp_path), "bin", "proot")
    assert ProotDistro(config).config is config


@pytest.mark.parametrize("runtime,cache", [
    ("", "cache"),
    ("state", ""),
    (os.path.sep, "cache"),
    ("state", os.path.sep),
    ("same", "same"),
    ("cache/state", "cache"),
    ("bad\0state", "cache"),
])
def test_runtime_config_rejects_unsafe_roots(runtime, cache):
    with pytest.raises(InvalidArgumentError) as exc:
        RuntimeConfig(runtime, cache)
    assert exc.value.operation == "configure"


def test_runtime_config_rejects_invalid_proot_path(tmp_path):
    with pytest.raises(InvalidArgumentError) as exc:
        RuntimeConfig(tmp_path / "state", tmp_path / "cache", "bad\0proot")
    assert exc.value.operation == "configure"


def test_worker_environment_carries_runtime_config(tmp_path):
    config = RuntimeConfig(
        tmp_path / "state", tmp_path / "cache", tmp_path / "proot"
    )
    env = program._worker_environment(config)

    assert env["_PROOT_DISTRO_API_RUNTIME_DIR"] == config.runtime_dir
    assert env["_PROOT_DISTRO_API_CACHE_DIR"] == config.cache_dir
    assert env["PD_PROOT_BIN"] == config.proot_path


def test_worker_environment_does_not_inherit_cli_credentials(tmp_path,
                                                             monkeypatch):
    monkeypatch.setenv("PD_DOCKER_AUTH", "ambient:secret")
    config = RuntimeConfig(tmp_path / "state", tmp_path / "cache")
    assert "PD_DOCKER_AUTH" not in program._worker_environment(config)


def test_operation_worker_ignores_startup_hooks_and_cwd_package_shadow(
        tmp_path, monkeypatch):
    site_marker = tmp_path / "worker-sitecustomize-ran"
    shadow_marker = tmp_path / "worker-shadow-package-loaded"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(site_marker)!r}).write_text('loaded')\n"
    )
    working = tmp_path / "working"
    shadow = working / "proot_distro"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(shadow_marker)!r}).write_text('loaded')\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(hooks))
    monkeypatch.setenv("PYTHONINSPECT", "1")
    monkeypatch.chdir(working)

    assert ProotDistro().list_containers() == ()
    assert not site_marker.exists()
    assert not shadow_marker.exists()


def test_empty_inventories_return_tuples(capsys):
    pd = ProotDistro()

    assert pd.list_containers() == ()
    assert pd.list_images() == ()
    assert pd.list_sessions() == ()
    assert capsys.readouterr() == ("", "")


def test_container_inventory_returns_recorded_origin(builders):
    digest = "sha256:" + "ab" * 32
    manifest = builders.simple_image_manifest("example/app:1", "aarch64")
    manifest["manifest"]["config"] = {"digest": digest}
    builders.make_container("app", arch="aarch64", manifest=manifest)

    assert ProotDistro().list_containers() == (
        ContainerInfo("app", "example/app:1", "aarch64", digest),
    )


def test_plain_container_has_no_image_metadata(builders):
    builders.make_container("plain")
    assert ProotDistro().list_containers() == (
        ContainerInfo("plain", None, None, None),
    )


def test_image_inventory_returns_immutable_model(builders):
    layer_digest, layer_size, _diff_id = builders.seed_cached_layer([
        {"name": "file", "type": "file", "data": b"layer data"},
    ])
    layer = {"digest": layer_digest, "size": layer_size}
    config = {"architecture": "amd64", "created": "2026-01-02T03:04:05Z"}
    config_bytes = canonical_json(config)
    digest = "sha256:" + hashlib.sha256(config_bytes).hexdigest()
    manifest = {
        "schemaVersion": 2,
        "config": {"digest": digest, "size": len(config_bytes)},
        "layers": [layer],
    }
    save_manifest_cache("example/app:1", "x86_64", manifest,
                        "example/app", config)

    images = ProotDistro().list_images()

    assert len(images) == 1
    image = images[0]
    assert isinstance(image, ImageInfo)
    assert image.identifier == "example/app:1"
    assert image.reference == "example/app:1"
    assert image.architecture == "x86_64"
    assert image.image_id == digest
    assert image.size_bytes == layer["size"]
    assert image.layer_count == 1
    assert image.missing_layers == 0
    assert image.complete is True
    with pytest.raises(AttributeError):
        image.identifier = "changed"


def test_session_inventory_copies_mutable_command(monkeypatch):
    command = ["/bin/sh", "-l"]
    monkeypatch.setattr(program, "_run_worker", lambda *_args, **_kwargs: {
        "items": [{
            "pid": 123,
            "container": "box",
            "kind": "login",
            "command": command,
            "user": "root",
            "start_time": 10.5,
            "isolated": True,
            "minimal": False,
            "detached": True,
        }],
    })

    sessions = ProotDistro().list_sessions()
    command.append("changed")

    assert sessions == (
        SessionInfo(123, "box", "login", ("/bin/sh", "-l"), "root",
                    10.5, True, False, True),
    )


@pytest.mark.parametrize("query", [None, 1, [], "", "   ", "bad\udcff"])
def test_search_rejects_invalid_query(query):
    with pytest.raises(InvalidArgumentError) as exc:
        ProotDistro().search(query)
    assert exc.value.operation == "search"


@pytest.mark.parametrize("limit", [True, 1.5, "2", 0, 1001])
def test_search_rejects_invalid_limit(limit):
    with pytest.raises(InvalidArgumentError) as exc:
        ProotDistro().search("alpine", limit=limit)
    assert exc.value.operation == "search"


def test_search_returns_models_without_printing(monkeypatch, capsys):
    monkeypatch.setattr(program, "search_images", lambda query, limit, **kw: (
        [{"name": "alpine", "description": "small", "stars": 7,
          "pulls": 11, "official": True, "automated": False}],
        20,
    ))

    result = ProotDistro().search(" alpine ", limit=3)

    assert result == SearchResult(
        query="alpine",
        total=20,
        hits=(SearchHit("alpine", "small", 7, 11, True, False),),
    )
    assert capsys.readouterr() == ("", "")


def test_search_forwards_retry_as_event(monkeypatch):
    events = []

    def fake_search(query, limit, *, on_retry):
        on_retry("retry notice")
        return [], 0

    monkeypatch.setattr(program, "search_images", fake_search)
    result = ProotDistro(event_handler=events.append).search("none")

    assert result.hits == ()
    assert events == [OperationEvent(
        "search", EventKind.RETRY, "retry notice",
        phase="network", resource="none",
    )]


def test_search_suppresses_retry_without_handler(monkeypatch):
    def fake_search(query, limit, *, on_retry):
        assert on_retry is None
        return [], 0

    monkeypatch.setattr(program, "search_images", fake_search)
    ProotDistro().search("none")


def test_credential_provider_receives_canonical_registry_request(monkeypatch):
    requests = []
    worker_calls = []

    def provider(request):
        requests.append(request)
        return RegistryCredentials("alice", "secret")

    def worker(operation, options, *_args, **_kwargs):
        worker_calls.append((operation, options))
        if operation == "install":
            return {"container_name": "alpine"}
        return {"items": [{
            "name": "alpine", "image_reference": "alpine:latest",
            "architecture": "x86_64", "image_id": None,
        }]}

    monkeypatch.setattr(program, "_run_worker", worker)
    result = ProotDistro(credential_provider=provider).install("alpine")

    assert result.name == "alpine"
    assert requests == [CredentialRequest(
        registry="docker.io",
        repository="library/alpine",
        operation="install",
    )]
    install_options = worker_calls[0][1]
    assert install_options["credentials"] == {
        "username": "alice", "password": "secret",
    }


def test_credential_provider_is_not_called_for_local_install(tmp_path,
                                                             monkeypatch):
    archive = tmp_path / "rootfs.tar"

    def provider(_request):
        pytest.fail("local installs must not request registry credentials")

    def worker(operation, _options, *_args, **_kwargs):
        if operation == "install":
            return {"container_name": "local"}
        return {"items": [{
            "name": "local", "image_reference": None,
            "architecture": None, "image_id": None,
        }]}

    monkeypatch.setattr(program, "_run_worker", worker)
    ProotDistro(credential_provider=provider).install(archive, name="local")


def test_credential_provider_must_return_credentials(monkeypatch):
    monkeypatch.setattr(
        program, "_run_worker",
        lambda *_args, **_kwargs: pytest.fail("worker should not start"),
    )
    with pytest.raises(InvalidArgumentError) as exc:
        ProotDistro(credential_provider=lambda _request: "alice:secret").install(
            "alpine"
        )
    assert exc.value.operation == "install"


def test_reset_resolves_credentials_for_recorded_image(monkeypatch):
    requests = []
    reset_options = []

    def provider(request):
        requests.append(request)
        return RegistryCredentials("alice", "secret")

    def worker(operation, options, *_args, **_kwargs):
        if operation == "list_containers":
            return {"items": [{
                "name": "box",
                "image_reference": "ghcr.io/example/private:1",
                "architecture": "x86_64",
                "image_id": None,
            }]}
        if operation == "reset_container":
            reset_options.append(options)
            return {"container_name": "box"}
        raise AssertionError(operation)

    monkeypatch.setattr(program, "_run_worker", worker)
    result = ProotDistro(credential_provider=provider).reset_container("box")

    assert result.name == "box"
    assert requests == [CredentialRequest(
        registry="ghcr.io",
        repository="example/private",
        operation="reset_container",
    )]
    assert reset_options == [{
        "name": "box",
        "credentials": {"username": "alice", "password": "secret"},
    }]


def test_search_translates_network_error(monkeypatch):
    def fail(*args, **kwargs):
        from urllib.error import URLError
        raise URLError("offline")

    monkeypatch.setattr(program, "search_images", fail)
    with pytest.raises(NetworkError) as exc:
        ProotDistro().search("alpine")
    assert exc.value.operation == "search"


def test_search_translates_operation_error(monkeypatch):
    def fail(*args, **kwargs):
        raise RuntimeError("bad response")

    monkeypatch.setattr(program, "search_images", fail)
    with pytest.raises(OperationError) as exc:
        ProotDistro().search("alpine")
    assert exc.value.operation == "search"


def test_public_errors_share_one_base():
    assert issubclass(AlreadyExistsError, ProotDistroError)
    assert issubclass(AuthenticationError, ProotDistroError)
    assert issubclass(BusyError, ProotDistroError)
    assert issubclass(IntegrityError, ProotDistroError)
    assert issubclass(InvalidArgumentError, ProotDistroError)
    assert issubclass(NetworkError, ProotDistroError)
    assert issubclass(NotFoundError, ProotDistroError)
    assert issubclass(OperationError, ProotDistroError)
    assert issubclass(OperationCancelledError, OperationError)
    assert issubclass(PartialOperationError, ProotDistroError)
    assert issubclass(UnsupportedOperationError, ProotDistroError)
    assert issubclass(SessionStartupTimeoutError, OperationError)


@pytest.mark.parametrize("kind,exception_type", [
    ("already_exists", AlreadyExistsError),
    ("authentication", AuthenticationError),
    ("busy", BusyError),
    ("integrity", IntegrityError),
    ("network", NetworkError),
    ("not_found", NotFoundError),
    ("operation", OperationError),
    ("partial", PartialOperationError),
    ("unsupported", UnsupportedOperationError),
])
def test_structured_worker_error_mapping(kind, exception_type):
    payload = (
        '{"_error":{"kind":"' + kind + '","message":"detail"}}'
    ).encode()
    error = program._structured_worker_error("test", payload)
    assert isinstance(error, exception_type)
    assert error.operation == "test"


@pytest.mark.parametrize("payload", [
    b"",
    b"null",
    b"[]",
    b"{}",
    b'{"_error":null}',
    b'{"_error":{"kind":"unknown","message":"detail"}}',
    b'{"_error":{"kind":"operation","message":""}}',
    b'{"_error":{"kind":"operation","message":1}}',
    b"\xff",
    pytest.param(_DEEPLY_NESTED_JSON, id="deeply-nested"),
])
def test_structured_worker_error_rejects_malformed_envelopes(payload):
    assert program._structured_worker_error("test", payload) is None


def _mock_worker_result(monkeypatch, stdout, *, returncode=0, stderr=b""):
    class Process:
        pid = 321

        def __init__(self):
            self.returncode = returncode

        def wait(self):
            return self.returncode

    def communicate(
        _process, _request, event_fd, _handler, _operation, **_kwargs
    ):
        os.close(event_fd)
        return stdout, stderr

    monkeypatch.setattr(program.subprocess, "Popen", lambda *_a, **_k: Process())
    monkeypatch.setattr(program, "_communicate_with_channels", communicate)
    return program._run_worker("list_images", {})


@pytest.mark.parametrize("payload", [
    b"",
    b"null",
    b"[]",
    b"not json",
    b'{"_events":null}',
    b'{"_events":[{"kind":"info","message":"late"}]}',
    b"\xff",
    pytest.param(_DEEPLY_NESTED_JSON, id="deeply-nested"),
])
def test_worker_result_rejects_malformed_documents(monkeypatch, payload):
    with pytest.raises(OperationError) as exc:
        _mock_worker_result(monkeypatch, payload)

    assert exc.value.operation == "list_images"
    assert "Traceback" not in str(exc.value)


def test_worker_result_accepts_document_at_size_limit(monkeypatch):
    prefix = b'{"value":1}'
    payload = prefix + b" " * (program._MAX_WORKER_RESULT_BYTES - len(prefix))

    result = _mock_worker_result(monkeypatch, payload)

    assert result == {"value": 1}


def test_worker_result_rejects_document_over_size_limit(monkeypatch):
    prefix = b'{"value":1}'
    payload = prefix + b" " * (
        program._MAX_WORKER_RESULT_BYTES + 1 - len(prefix)
    )

    with pytest.raises(OperationError) as exc:
        _mock_worker_result(monkeypatch, payload)

    assert exc.value.operation == "list_images"
    assert "oversized result" in str(exc.value)


@pytest.mark.parametrize("payload", [
    b"",
    b"null",
    b"[]",
    b"not json",
    b"\xff",
    b"{}",
    b'{"operation":"list_images","options":[]}',
    b'{"operation":"unknown","options":{}}',
    pytest.param(_DEEPLY_NESTED_JSON, id="deeply-nested"),
])
def test_worker_rejects_malformed_request_document(monkeypatch, payload):
    class Input:
        buffer = io.BytesIO(payload)

    monkeypatch.setattr(_worker.sys, "stdin", Input())

    with pytest.raises((ValueError, UnicodeDecodeError, RecursionError)):
        _worker._read_request()


def test_worker_rejects_oversized_request_document(monkeypatch):
    payload = b"x" * (_worker._MAX_INPUT_BYTES + 1)

    class Input:
        buffer = io.BytesIO(payload)

    monkeypatch.setattr(_worker.sys, "stdin", Input())

    with pytest.raises(ValueError) as exc:
        _worker._read_request()

    assert "too large" in str(exc.value)


def test_worker_rejects_unknown_operation_without_traceback():
    with pytest.raises(OperationError) as exc:
        program._run_worker("unknown", {})
    assert exc.value.operation == "unknown"
    assert "not valid" in str(exc.value)
    assert "Traceback" not in str(exc.value)


def test_credential_service_rejects_malformed_request_without_deadlock():
    parent, worker = socket.socketpair()
    try:
        worker.sendall(b"not json\n")
        error = program._serve_credential_requests(
            parent, lambda _request: None, "build"
        )
        response = worker.recv(4096)
    finally:
        parent.close()
        worker.close()

    assert isinstance(error, OperationError)
    assert b"credential provider failed" in response


@pytest.mark.parametrize("raw_request", [
    b"",
    b"[]",
    b"{}",
    b'{"registry":null,"repository":"team/app","actions":"pull"}',
    b'{"registry":"example.test","repository":"","actions":"pull"}',
    b'{"registry":"example.test","repository":"team/app","actions":[]}',
    b"\xff",
    pytest.param(_DEEPLY_NESTED_JSON, id="deeply-nested"),
])
def test_credential_request_rejects_malformed_fields(raw_request):
    parent, worker = socket.socketpair()
    called = []
    try:
        with pytest.raises(OperationError) as exc:
            program._handle_credential_request(
                raw_request, parent, called.append, "push"
            )
        response = worker.recv(4096)
    finally:
        parent.close()
        worker.close()

    assert exc.value.operation == "push"
    assert called == []
    assert b"credential provider failed" in response


def test_credential_request_service_rejects_truncated_frame():
    parent, worker = socket.socketpair()
    try:
        worker.sendall(b'{"registry":"example.test"}')
        worker.shutdown(socket.SHUT_WR)
        error = program._serve_credential_requests(
            parent, lambda _request: None, "build"
        )
    finally:
        parent.close()
        worker.close()

    assert isinstance(error, OperationError)
    assert error.operation == "build"
    assert "truncated request" in str(error)


def test_credential_request_service_rejects_oversized_frame():
    parent, worker = socket.socketpair()
    response = []

    def send_request():
        try:
            worker.sendall(b"x" * (program._MAX_CREDENTIAL_FRAME_BYTES + 1))
            response.append(worker.recv(4096))
        finally:
            worker.close()

    sender = threading.Thread(target=send_request)
    sender.start()
    try:
        error = program._serve_credential_requests(
            parent, lambda _request: None, "install"
        )
    finally:
        parent.close()
        sender.join(5)

    assert not sender.is_alive()
    assert isinstance(error, OperationError)
    assert error.operation == "install"
    assert "too large" in str(error)
    assert response and b"credential provider failed" in response[0]


def _credential_response(response):
    parent, worker = socket.socketpair()

    def serve():
        try:
            request = bytearray()
            while b"\n" not in request:
                chunk = worker.recv(4096)
                if not chunk:
                    return
                request.extend(chunk)
            if response:
                worker.sendall(response)
        finally:
            worker.close()

    server = threading.Thread(target=serve)
    server.start()
    channel = _worker._CredentialChannel(parent.detach())
    try:
        return channel("example.test", "team/app", "pull")
    finally:
        channel.close()
        server.join(5)
        assert not server.is_alive()


@pytest.mark.parametrize("response,detail", [
    (b"not json\n", "invalid"),
    (b"[]\n", "failed"),
    (b'{"error":"no"}\n', "failed"),
    (b'{"credentials":[]}\n', "invalid"),
    (b'{"credentials":{"username":1,"password":"secret"}}\n', "invalid"),
    (b'{"credentials":{"username":"alice","password":null}}\n', "invalid"),
    (b"\xff\n", "invalid"),
    pytest.param(
        _DEEPLY_NESTED_JSON + b"\n", "failed", id="deeply-nested"
    ),
])
def test_worker_rejects_malformed_credential_response(response, detail):
    with pytest.raises(RuntimeError) as exc:
        _credential_response(response)

    assert detail in str(exc.value)


def test_worker_rejects_truncated_credential_response():
    with pytest.raises(RuntimeError) as exc:
        _credential_response(b'{"credentials":null}')

    assert "closed" in str(exc.value)


def test_worker_rejects_oversized_credential_response():
    response = b"x" * (_worker._MAX_CREDENTIAL_RESPONSE_BYTES + 1)

    with pytest.raises(RuntimeError) as exc:
        _credential_response(response)

    assert "too large" in str(exc.value)


def test_worker_event_frame_preserves_progress_context():
    event = program._operation_event("push", json.dumps({
        "kind": "progress",
        "message": "uploading",
        "current": 10,
        "total": 20,
        "unit": "bytes",
        "phase": "upload",
        "resource": "sha256:abc",
    }).encode())

    assert event == OperationEvent(
        "push", EventKind.PROGRESS, "uploading", 10, 20, "bytes",
        "upload", "sha256:abc",
    )


def test_worker_event_frame_rejects_invalid_context():
    with pytest.raises(OperationError) as exc:
        program._operation_event("copy", b'{"kind":"info","message":1}')

    assert exc.value.operation == "copy"


def _read_worker_event_channel(monkeypatch, payload):
    event_read, event_write = os.pipe()
    finished = threading.Event()

    def write_events():
        try:
            program._write_all(event_write, payload)
        except BrokenPipeError:
            pass
        finally:
            os.close(event_write)
            finished.set()

    writer = threading.Thread(target=write_events)
    writer.start()

    class Process:
        pid = 321

        def communicate(self, _request):
            assert finished.wait(5)
            return b"{}", b""

    monkeypatch.setattr(program.os, "killpg", lambda *_args: None)
    try:
        return program._communicate_with_channels(
            Process(), b"{}", event_read, None, "copy"
        )
    finally:
        writer.join(5)
        assert not writer.is_alive()


@pytest.mark.parametrize("payload", [
    b"\n",
    b"not json\n",
    b"[]\n",
    b"{}\n",
    b'{"kind":"unknown","message":"text"}\n',
    b'{"kind":"info","message":null}\n',
    b'{"kind":"progress","message":"x","current":true}\n',
    b'{"kind":"progress","message":"x","total":1.5}\n',
    b'{"kind":"info","message":"x","phase":1}\n',
    b'{"kind":"info","message":"x","resource":[]}\n',
    b"\xff\n",
    pytest.param(
        _DEEPLY_NESTED_JSON + b"\n", id="deeply-nested"
    ),
])
def test_worker_event_channel_rejects_malformed_frames(monkeypatch, payload):
    with pytest.raises(OperationError) as exc:
        _read_worker_event_channel(monkeypatch, payload)

    assert exc.value.operation == "copy"
    assert "invalid event" in str(exc.value)


def test_worker_event_channel_rejects_truncated_frame(monkeypatch):
    with pytest.raises(OperationError) as exc:
        _read_worker_event_channel(
            monkeypatch, b'{"kind":"info","message":"partial"}'
        )

    assert exc.value.operation == "copy"
    assert "truncated event" in str(exc.value)


def test_worker_event_channel_rejects_oversized_frame(monkeypatch):
    payload = b"x" * (program._MAX_WORKER_EVENT_BYTES + 1)

    with pytest.raises(OperationError) as exc:
        _read_worker_event_channel(monkeypatch, payload)

    assert exc.value.operation == "copy"
    assert "oversized event" in str(exc.value)


def test_worker_event_writer_enforces_frame_limit():
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(RuntimeError) as exc:
            _worker._write_event(write_fd, {
                "kind": "info",
                "message": "x" * _worker._MAX_EVENT_BYTES,
            })
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert "too large" in str(exc.value)


def test_slow_event_handler_applies_backpressure_and_recovers():
    event_read, event_write = os.pipe()
    entered = threading.Event()
    release = threading.Event()
    writer_finished = threading.Event()
    call_finished = threading.Event()
    events = []
    result = {}
    frame = (
        b'{"kind":"info","message":"event payload for backpressure"}\n'
    )
    frame_count = 20000

    def write_events():
        try:
            program._write_all(event_write, frame * frame_count)
        finally:
            os.close(event_write)
            writer_finished.set()

    writer = threading.Thread(target=write_events)
    writer.start()

    class Process:
        pid = 321

        def communicate(self, _request):
            assert writer_finished.wait(10)
            return b"{}", b""

    def handle(event):
        events.append(event)
        if len(events) == 1:
            entered.set()
            assert release.wait(10)

    def call():
        try:
            result["streams"] = program._communicate_with_channels(
                Process(), b"{}", event_read, handle, "copy"
            )
        except BaseException as exc:
            result["error"] = exc
        finally:
            call_finished.set()

    caller = threading.Thread(target=call)
    caller.start()
    try:
        assert entered.wait(5)
        assert not writer_finished.wait(0.1)
        assert not call_finished.is_set()
    finally:
        release.set()
    caller.join(15)
    writer.join(15)

    assert not caller.is_alive()
    assert not writer.is_alive()
    assert "error" not in result
    assert result["streams"] == (b"{}", b"")
    assert len(events) == frame_count


def test_worker_event_resource_strips_url_secrets():
    resource = _worker._default_event_resource("install", {
        "source": "https://user:secret@example.test/rootfs.tar?token=hidden#x",
        "name": None,
    })

    assert resource == "https://example.test/rootfs.tar"


def test_worker_event_resource_prefers_public_operation_subject():
    assert _worker._default_event_resource("copy", {
        "source": "/host/source",
        "destination": "box:/srv/data",
    }) == "box:/srv/data"


def test_worker_channel_cancellation_interrupts_and_drains(monkeypatch):
    event_read, event_write = os.pipe()
    stopped = threading.Event()
    signals = []

    class Process:
        pid = 321

        def communicate(self, _request):
            assert stopped.wait(5)
            return b"", b""

    def interrupt(pid, selected_signal):
        signals.append((pid, selected_signal))
        stopped.set()
        os.close(event_write)

    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(program.os, "killpg", interrupt)

    with pytest.raises(OperationCancelledError) as exc:
        program._communicate_with_channels(
            Process(), b"{}", event_read, None, "copy",
            cancel_event=cancelled,
        )

    assert exc.value.operation == "copy"
    assert signals == [(321, program.signal.SIGINT)]


def test_pre_cancelled_worker_does_not_start(monkeypatch):
    cancelled = threading.Event()
    cancelled.set()
    monkeypatch.setattr(
        program.subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("worker must not start"),
    )

    with pytest.raises(OperationCancelledError) as exc:
        program._run_worker("list_images", {}, cancel_event=cancelled)

    assert exc.value.operation == "list_images"


@pytest.mark.parametrize("value", [None, 0, 1, "false", (), object()])
def test_cancellation_token_must_return_boolean(monkeypatch, value):
    class InvalidCancellation:
        def is_set(self):
            return value

    monkeypatch.setattr(
        program.subprocess, "Popen",
        lambda *_args, **_kwargs: pytest.fail("worker must not start"),
    )

    with pytest.raises(InvalidArgumentError) as exc:
        ProotDistro().list_images(cancel_event=InvalidCancellation())

    assert exc.value.operation == "list_images"


@pytest.mark.parametrize("result", [None, True, False, 0, -1, 4, 1.5])
def test_output_stream_rejects_invalid_write_result(result):
    class Output:
        def write(self, _data):
            return result

    with pytest.raises(InvalidArgumentError) as exc:
        program._write_stream_chunk(Output(), b"abc", "backup")

    assert exc.value.operation == "backup"
    assert "positive byte count" in str(exc.value)


def test_output_stream_accepts_short_writes_until_chunk_is_complete():
    class Output:
        def __init__(self):
            self.data = bytearray()

        def write(self, data):
            size = min(2, len(data))
            self.data.extend(data[:size])
            return size

    output = Output()

    program._write_stream_chunk(output, b"abcdef", "backup")

    assert output.data == b"abcdef"


@pytest.mark.parametrize("result", [None, "text", 1, object()])
def test_input_stream_rejects_non_bytes_read_result(
    monkeypatch, result,
):
    class Process:
        pid = 321

        def wait(self, timeout=None):
            return 0

    class Input:
        def read(self, _size):
            return result

    signals = []
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(
        program.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )
    try:
        threads, errors, _counts = program._start_stream_pumps(
            Process(), [(write_fd, Input())], [], "restore"
        )
        for thread in threads:
            thread.join(5)
            assert not thread.is_alive()
    finally:
        os.close(read_fd)

    assert len(errors) == 1
    assert isinstance(errors[0], InvalidArgumentError)
    assert errors[0].operation == "restore"
    assert signals == [(321, program.signal.SIGINT)]


@pytest.mark.parametrize("result", [None, True, 0, -1, 4, 1.5])
def test_output_stream_invalid_result_stops_worker(
    monkeypatch, result,
):
    class Process:
        pid = 321

        def wait(self, timeout=None):
            return 0

    class Output:
        def write(self, _data):
            return result

    signals = []
    read_fd, write_fd = os.pipe()
    monkeypatch.setattr(
        program.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )
    threads, errors, counts = program._start_stream_pumps(
        Process(), [], [("backup_output", read_fd, Output())], "backup"
    )
    try:
        os.write(write_fd, b"abc")
    finally:
        os.close(write_fd)
    for thread in threads:
        thread.join(5)
        assert not thread.is_alive()

    assert len(errors) == 1
    assert isinstance(errors[0], InvalidArgumentError)
    assert errors[0].operation == "backup"
    assert counts == {}
    assert signals == [(321, program.signal.SIGINT)]


def test_worker_stop_allows_cooperative_cleanup(monkeypatch):
    class Process:
        pid = 321

        def __init__(self):
            self.timeouts = []

        def wait(self, timeout=None):
            self.timeouts.append(timeout)
            return 130

    process = Process()
    signals = []
    monkeypatch.setattr(
        program.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )

    program._stop_worker(process)

    assert process.timeouts == [program._WORKER_STOP_GRACE_SECONDS]
    assert signals == [(321, program.signal.SIGINT)]


def test_worker_stop_escalates_after_cleanup_timeout(monkeypatch):
    class Process:
        pid = 321

        def wait(self, timeout=None):
            raise subprocess.TimeoutExpired("worker", timeout)

    signals = []
    monkeypatch.setattr(
        program.os, "killpg", lambda pid, sig: signals.append((pid, sig))
    )

    program._stop_worker(Process())

    assert signals == [
        (321, program.signal.SIGINT),
        (321, program.signal.SIGKILL),
    ]


def test_worker_backed_public_methods_accept_cancellation():
    methods = (
        "list_containers", "list_images", "list_sessions", "install",
        "remove_container", "rename_container", "reset_container", "copy",
        "move", "sync", "backup", "restore", "build", "push",
        "remove_image", "clear_cache", "start_detached", "run_detached",
        "inspect_start", "inspect_run", "kill_session", "kill_all_sessions",
    )

    for name in methods:
        assert "cancel_event" in inspect.signature(
            getattr(ProotDistro, name)
        ).parameters


def test_pre_cancelled_install_does_not_request_credentials():
    cancelled = threading.Event()
    cancelled.set()

    def credentials(_request):
        pytest.fail("credential provider must not be called")

    with pytest.raises(OperationCancelledError) as exc:
        ProotDistro(credential_provider=credentials).install(
            "example.test/team/image:1", cancel_event=cancelled
        )

    assert exc.value.operation == "install"


def test_session_process_falls_back_when_registry_has_no_record():
    class Owner:
        def kill_session(self, pid, *, signal_name):
            return SessionSignalResult(str(pid), "SIG" + signal_name, ())

    class Process:
        pid = 123
        returncode = None
        stdin = stdout = stderr = None

        def __init__(self):
            self.terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    raw = Process()
    handle = SessionProcess(Owner(), raw, container="box", kind="login")

    result = handle.terminate()

    assert result.pids == ()
    assert raw.terminated is True


def _read_startup_payload(payload, event_handler=None):
    read_fd, write_fd = os.pipe()

    def write_status():
        try:
            program._write_all(write_fd, payload)
        except BrokenPipeError:
            pass
        finally:
            os.close(write_fd)

    writer = threading.Thread(target=write_status)
    writer.start()
    try:
        return program._read_session_startup(
            read_fd, "start", event_handler, 5, None
        )
    finally:
        os.close(read_fd)
        writer.join(5)
        assert not writer.is_alive()


@pytest.mark.parametrize("payload", [
    b"\n",
    b"not json\n",
    b"[]\n",
    b"{}\n",
    b'{"error":""}\n',
    b'{"error":1}\n',
    b'{"event":null}\n',
    b'{"event":{"kind":"progress","message":"x"}}\n',
    b'{"event":{"kind":"info","message":1}}\n',
    b'{"event":{"kind":"info","message":"x","phase":1}}\n',
    b'{"event":{"kind":"warning","message":"x","resource":[]}}\n',
    b"\xff\n",
    pytest.param(
        _DEEPLY_NESTED_JSON + b"\n", id="deeply-nested"
    ),
])
def test_session_startup_rejects_malformed_status_frames(payload):
    with pytest.raises(OperationError) as exc:
        _read_startup_payload(payload)

    assert exc.value.operation == "start"
    assert "invalid setup status" in str(exc.value)


def test_session_startup_rejects_truncated_status_frame():
    with pytest.raises(OperationError) as exc:
        _read_startup_payload(b'{"event":')

    assert exc.value.operation == "start"
    assert "truncated setup status" in str(exc.value)


def test_session_startup_rejects_oversized_status_frame():
    payload = b"x" * (program._MAX_SESSION_STATUS_BYTES + 1)

    with pytest.raises(OperationError) as exc:
        _read_startup_payload(payload)

    assert exc.value.operation == "start"
    assert "oversized setup status" in str(exc.value)


def test_session_startup_delivers_valid_event_and_error():
    events = []
    payload = (
        b'{"event":{"kind":"warning","message":"careful",'
        b'"phase":"setup","resource":"box"}}\n'
        b'{"error":"cannot execute guest"}\n'
    )

    error = _read_startup_payload(payload, events.append)

    assert error == "cannot execute guest"
    assert events == [OperationEvent(
        "start", EventKind.WARNING, "careful",
        phase="setup", resource="box",
    )]


def _read_session_request(payload):
    read_fd, write_fd = os.pipe()

    def write_request():
        try:
            program._write_all(write_fd, payload)
        except BrokenPipeError:
            pass
        finally:
            os.close(write_fd)

    writer = threading.Thread(target=write_request)
    writer.start()
    try:
        return _session_worker._read_request(read_fd)
    finally:
        os.close(read_fd)
        writer.join(5)
        assert not writer.is_alive()


@pytest.mark.parametrize("payload", [
    b"",
    b"null",
    b"[]",
    b"not json",
    b"\xff",
    pytest.param(_DEEPLY_NESTED_JSON, id="deeply-nested"),
])
def test_session_worker_rejects_malformed_request_document(payload):
    with pytest.raises((ValueError, UnicodeDecodeError, RecursionError)):
        _read_session_request(payload)


def test_session_worker_rejects_oversized_request_document():
    payload = b"x" * (_session_worker._MAX_REQUEST_BYTES + 1)

    with pytest.raises(ValueError) as exc:
        _read_session_request(payload)

    assert "too large" in str(exc.value)


@pytest.mark.parametrize("session_request", [
    {},
    {"kind": "login", "container": "box", "arguments": [], "options": []},
    {"kind": "invalid", "container": "box", "arguments": [], "options": {}},
    {"kind": "login", "container": None, "arguments": [], "options": {}},
    {"kind": "run", "container": "box", "arguments": (), "options": {}},
])
def test_session_worker_rejects_malformed_request_fields(session_request):
    with pytest.raises(ValueError):
        _session_worker._request_parts(session_request)


def test_session_worker_status_writer_enforces_frame_limit():
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(ValueError) as exc:
            _session_worker._write_frame(write_fd, {
                "error": "x" * _session_worker._MAX_STATUS_BYTES,
            })
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert "too large" in str(exc.value)


def test_session_worker_replaces_oversized_error_with_bounded_status(
    monkeypatch,
):
    request_read, request_write = os.pipe()
    status_read, status_write = os.pipe()
    descriptors = iter((request_read, status_write))
    monkeypatch.setattr(
        _session_worker, "_fd_from_env", lambda _name: next(descriptors)
    )
    monkeypatch.setattr(
        _session_worker, "_read_request",
        lambda _fd: (_ for _ in ()).throw(
            ValueError("x" * _session_worker._MAX_STATUS_BYTES)
        ),
    )
    try:
        assert _session_worker.main() == 1
        frame = os.read(status_read, _session_worker._MAX_STATUS_BYTES)
    finally:
        os.close(request_write)
        os.close(status_read)

    assert json.loads(frame) == {
        "error": _session_worker._OVERSIZED_STATUS_ERROR,
    }


def test_session_startup_status_timeout_is_typed():
    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(SessionStartupTimeoutError) as exc:
            program._read_session_startup(
                read_fd, "start", None, 0.001, None
            )
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert exc.value.operation == "start"


def test_session_startup_status_observes_cancellation():
    class Cancellation:
        def __init__(self):
            self.calls = 0

        def is_set(self):
            self.calls += 1
            return self.calls > 1

    read_fd, write_fd = os.pipe()
    try:
        with pytest.raises(OperationCancelledError) as exc:
            program._read_session_startup(
                read_fd, "run", None, None, Cancellation()
            )
    finally:
        os.close(read_fd)
        os.close(write_fd)

    assert exc.value.operation == "run"


def test_public_session_timeout_kills_and_reaps_setup_worker(monkeypatch):
    class StalledWorker:
        instances = []

        def __init__(self, _argv, **kwargs):
            request_fd, status_fd = kwargs["pass_fds"]
            self.request_fd = os.dup(request_fd)
            self.status_fd = os.dup(status_fd)
            self.pid = 123
            self.returncode = None
            self.killed = False
            self.waited = False
            self.__class__.instances.append(self)

        def kill(self):
            self.killed = True
            self.returncode = -9
            for name in ("request_fd", "status_fd"):
                fd = getattr(self, name)
                if fd is not None:
                    os.close(fd)
                    setattr(self, name, None)

        def wait(self):
            self.waited = True
            return self.returncode

    pd = ProotDistro()
    monkeypatch.setattr(pd, "list_containers", lambda: (
        ContainerInfo("box", None, None, None),
    ))
    monkeypatch.setattr(program.subprocess, "Popen", StalledWorker)

    with pytest.raises(SessionStartupTimeoutError):
        pd.start("box", startup_timeout=0.001)

    worker = StalledWorker.instances[0]
    assert worker.killed is True
    assert worker.waited is True
