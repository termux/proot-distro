import hashlib
import subprocess
import sys

import pytest

from proot_distro.api import (
    ContainerInfo,
    EventKind,
    ImageInfo,
    InvalidArgumentError,
    NetworkError,
    OperationError,
    OperationEvent,
    ProotDistro,
    ProotDistroError,
    SearchHit,
    SearchResult,
    SessionInfo,
)
from proot_distro.api import program
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import canonical_json


def test_public_exports_are_explicit():
    import proot_distro.api as api

    assert set(api.__all__) == {
        "ContainerInfo", "EventKind", "ImageInfo", "InvalidArgumentError",
        "NetworkError", "OperationError", "OperationEvent", "ProotDistro",
        "ProotDistroError", "SearchHit", "SearchResult", "SessionInfo",
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
    monkeypatch.setattr(program, "active_sessions", lambda: [{
        "pid": 123,
        "container": "box",
        "kind": "login",
        "command": command,
        "user": "root",
        "start_time": 10.5,
        "isolated": True,
        "minimal": False,
        "detach": True,
    }])

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
    assert issubclass(InvalidArgumentError, ProotDistroError)
    assert issubclass(NetworkError, ProotDistroError)
    assert issubclass(OperationError, ProotDistroError)


def test_worker_rejects_unknown_operation_without_traceback():
    with pytest.raises(OperationError) as exc:
        program._run_worker("unknown", {})
    assert exc.value.operation == "unknown"
    assert "not valid" in str(exc.value)
    assert "Traceback" not in str(exc.value)
