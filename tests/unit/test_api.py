import hashlib
import os
import subprocess
import sys

import pytest

from proot_distro.api import (
    AuthenticationError,
    BackupResult,
    ContainerInfo,
    CredentialProvider,
    CredentialRequest,
    EventKind,
    ImageInfo,
    InvalidArgumentError,
    NetworkError,
    OperationError,
    OperationEvent,
    ProotDistro,
    ProotDistroError,
    RegistryCredentials,
    RestoreResult,
    RuntimeConfig,
    SearchHit,
    SearchResult,
    SessionInfo,
    TransferResult,
)
from proot_distro.api import program
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import canonical_json


def test_public_exports_are_explicit():
    import proot_distro.api as api

    assert set(api.__all__) == {
        "AuthenticationError", "BackupResult", "ContainerInfo", "CredentialProvider",
        "CredentialRequest", "EventKind", "ImageInfo",
        "InvalidArgumentError", "NetworkError", "OperationError",
        "OperationEvent", "ProotDistro", "ProotDistroError",
        "RegistryCredentials", "RestoreResult", "RuntimeConfig", "SearchHit",
        "SearchResult", "SessionInfo", "TransferResult",
    }


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
    assert events == [OperationEvent("search", EventKind.RETRY, "retry notice")]


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
    assert issubclass(AuthenticationError, ProotDistroError)
    assert issubclass(InvalidArgumentError, ProotDistroError)
    assert issubclass(NetworkError, ProotDistroError)
    assert issubclass(OperationError, ProotDistroError)


def test_worker_rejects_unknown_operation_without_traceback():
    with pytest.raises(OperationError) as exc:
        program._run_worker("unknown", {})
    assert exc.value.operation == "unknown"
    assert "not valid" in str(exc.value)
    assert "Traceback" not in str(exc.value)
