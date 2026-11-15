"""Behavioural tests for public API methods, with a scripted worker."""

import pytest

from proot_distro.api import (
    InvalidArgumentError,
    OperationError,
    ProotDistro,
    PushResult,
    SessionSignalResult,
    TransferResult,
)
from proot_distro.api import program


class _Worker:
    """Replace the worker runner; record calls and answer by operation."""

    def __init__(self, monkeypatch, answers):
        self.calls = []
        self.answers = answers
        monkeypatch.setattr(program, "_run_worker", self)

    def __call__(
        self, operation, options, config=None, event_handler=None,
        credential_provider=None, input_streams=None, output_streams=None,
        cancel_event=None, reported_operation=None,
    ):
        self.calls.append((operation, dict(options)))
        answer = self.answers[operation]
        return answer(options) if callable(answer) else answer


def test_kill_all_sessions_signals_everything_and_returns_pids(monkeypatch):
    worker = _Worker(monkeypatch, {"kill_session": {"pids": [11, 12]}})

    result = ProotDistro().kill_all_sessions(signal_name="KILL")

    assert result == SessionSignalResult("*", "SIGKILL", (11, 12))
    assert worker.calls == [("kill_session", {
        "target": None, "all": True, "signal": "SIGKILL",
    })]


@pytest.mark.parametrize("pids", [None, [0], [True], ["1"], 5])
def test_kill_all_sessions_rejects_invalid_worker_result(monkeypatch, pids):
    _Worker(monkeypatch, {"kill_session": {"pids": pids}})

    with pytest.raises(OperationError) as exc:
        ProotDistro().kill_all_sessions()

    assert exc.value.operation == "kill_all_sessions"


def test_kill_all_sessions_rejects_unknown_signal(monkeypatch):
    worker = _Worker(monkeypatch, {})

    with pytest.raises(InvalidArgumentError):
        ProotDistro().kill_all_sessions(signal_name="NOPE")

    assert worker.calls == []


def test_push_maps_worker_result(monkeypatch):
    worker = _Worker(monkeypatch, {"push": {
        "registry": "registry.test", "repo": "team/app", "tag": "1",
        "manifest_digest": "sha256:" + "a" * 64, "bytes_uploaded": 42,
    }})

    result = ProotDistro().push("registry.test/team/app:1")

    assert result == PushResult(
        "registry.test", "team/app", "1", "sha256:" + "a" * 64, 42
    )
    operation, options = worker.calls[0]
    assert operation == "push"
    assert options["image_ref"] == "registry.test/team/app:1"
    assert options["credentials"] is None


@pytest.mark.parametrize("override", [
    {"registry": ""}, {"tag": 1}, {"bytes_uploaded": -1},
    {"bytes_uploaded": True}, {"manifest_digest": 5},
])
def test_push_rejects_invalid_worker_result(monkeypatch, override):
    answer = {
        "registry": "r.test", "repo": "a/b", "tag": "1",
        "manifest_digest": None, "bytes_uploaded": 1,
    }
    answer.update(override)
    _Worker(monkeypatch, {"push": answer})

    with pytest.raises(OperationError) as exc:
        ProotDistro().push("r.test/a/b:1")

    assert exc.value.operation == "push"


def _image(**changes):
    item = {
        "identifier": "abc", "reference": "alpine:3", "repository": "alpine",
        "architecture": "aarch64", "image_id": None, "size_bytes": 10,
        "layer_count": 2, "missing_layers": 0, "created": None,
        "cached_at": 1.0,
    }
    item.update(changes)
    return item


def test_remove_image_returns_removed_images(monkeypatch):
    worker = _Worker(monkeypatch, {"remove_image": {"removed": [_image()]}})

    result = ProotDistro().remove_image("alpine:3", architecture="arm64")

    assert [item.identifier for item in result.removed] == ["abc"]
    assert worker.calls[0][1]["architecture"] == "aarch64"


@pytest.mark.parametrize("removed", [None, "x", [{"identifier": "only"}]])
def test_remove_image_rejects_invalid_worker_result(monkeypatch, removed):
    _Worker(monkeypatch, {"remove_image": {"removed": removed}})

    with pytest.raises(OperationError) as exc:
        ProotDistro().remove_image("alpine:3")

    assert exc.value.operation == "remove_image"


def test_remove_image_rejects_bad_architecture(monkeypatch):
    worker = _Worker(monkeypatch, {})

    with pytest.raises(InvalidArgumentError):
        ProotDistro().remove_image("alpine:3", architecture="vax")

    assert worker.calls == []


def _container(name):
    return {
        "name": name, "image_reference": None, "architecture": None,
        "image_id": None,
    }


def test_rename_container_returns_new_metadata(monkeypatch):
    worker = _Worker(monkeypatch, {
        "rename_container": {},
        "list_containers": {"items": [_container("new-name")]},
    })

    result = ProotDistro().rename_container("old-name", "new-name")

    assert result.name == "new-name"
    assert worker.calls[0] == (
        "rename_container", {"old_name": "old-name", "new_name": "new-name"}
    )


def test_rename_container_rejects_same_name(monkeypatch):
    worker = _Worker(monkeypatch, {})

    with pytest.raises(InvalidArgumentError):
        ProotDistro().rename_container("same", "same")

    assert worker.calls == []


def test_rename_container_fails_if_target_is_missing_afterwards(monkeypatch):
    _Worker(monkeypatch, {
        "rename_container": {}, "list_containers": {"items": []},
    })

    with pytest.raises(OperationError) as exc:
        ProotDistro().rename_container("old-name", "new-name")

    assert exc.value.operation == "rename_container"


def test_sync_and_move_pass_options_and_echo_paths(monkeypatch, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    destination = str(tmp_path / "dst")
    worker = _Worker(monkeypatch, {
        "sync": lambda o: {"source": o["source"],
                           "destination": o["destination"]},
        "move": lambda o: {"source": o["source"],
                           "destination": o["destination"]},
    })
    pd = ProotDistro()

    synced = pd.sync(source, destination, checksum=True, delete=True)
    moved = pd.move(source, destination)

    assert synced == TransferResult("sync", str(source), destination)
    assert moved == TransferResult("move", str(source), destination)
    assert worker.calls[0][1]["checksum"] is True
    assert worker.calls[0][1]["delete"] is True
    assert worker.calls[1][0] == "move"


def test_transfer_rejects_worker_result_for_other_paths(monkeypatch, tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    _Worker(monkeypatch, {
        "copy": {"source": "/elsewhere", "destination": "/else"},
    })

    with pytest.raises(OperationError):
        ProotDistro().copy(source, str(tmp_path / "dst"))


def test_transfer_reports_missing_local_source_without_worker(
    monkeypatch, tmp_path,
):
    from proot_distro.api import NotFoundError

    worker = _Worker(monkeypatch, {})

    with pytest.raises(NotFoundError):
        ProotDistro().copy(tmp_path / "missing", str(tmp_path / "dst"))

    assert worker.calls == []


def test_backup_to_path_maps_worker_result(monkeypatch, tmp_path):
    output = str(tmp_path / "box.tar.gz")
    worker = _Worker(monkeypatch, {"backup": {
        "container_name": "box", "output": output,
        "compression": "gzip", "size_bytes": 99,
    }})

    result = ProotDistro().backup("box", output)

    assert (result.container, result.output, result.compression,
            result.size_bytes) == ("box", output, "gzip", 99)
    assert worker.calls[0][1]["name"] == "box"


def test_backup_rejects_result_for_another_container(monkeypatch, tmp_path):
    output = str(tmp_path / "box.tar.gz")
    _Worker(monkeypatch, {"backup": {
        "container_name": "other", "output": output,
        "compression": "gzip", "size_bytes": 1,
    }})

    with pytest.raises(OperationError) as exc:
        ProotDistro().backup("box", output)

    assert exc.value.operation == "backup"


def test_backup_rejects_unknown_compression(monkeypatch, tmp_path):
    worker = _Worker(monkeypatch, {})

    with pytest.raises(InvalidArgumentError):
        ProotDistro().backup(
            "box", str(tmp_path / "x"), compression="rar"
        )

    assert worker.calls == []
