import os

import pytest

from proot_distro.api import (
    AlreadyExistsError,
    BackupResult,
    BusyError,
    EventKind,
    InvalidArgumentError,
    IntegrityError,
    NotFoundError,
    UnsupportedOperationError,
    ProotDistro,
    RestoreResult,
    TransferResult,
)
from proot_distro.paths import container_rootfs


def test_copy_host_file_to_container(tmp_path, builders, capsys):
    builders.make_container("box")
    source = tmp_path / "source.txt"
    source.write_text("copied")

    result = ProotDistro().copy(source, "box:/root/copied.txt")

    assert result == TransferResult(
        "copy", str(source), "box:/root/copied.txt"
    )
    destination = os.path.join(container_rootfs("box"), "root", "copied.txt")
    assert open(destination).read() == "copied"
    assert capsys.readouterr() == ("", "")


def test_recursive_copy_emits_progress(tmp_path, builders):
    builders.make_container("box")
    source = tmp_path / "tree"
    source.mkdir()
    (source / "a").write_text("a")
    (source / "b").write_text("b")
    events = []

    ProotDistro(event_handler=events.append).copy(
        source, "box:/tree", recursive=True
    )

    assert any(event.kind == EventKind.PROGRESS for event in events)
    assert open(os.path.join(container_rootfs("box"), "tree", "a")).read() == "a"


def test_move_removes_source(tmp_path, builders):
    builders.make_container("box")
    source = tmp_path / "move.txt"
    source.write_text("moved")

    result = ProotDistro().move(source, "box:/root/moved.txt")

    assert result.operation == "move"
    assert not source.exists()
    assert open(os.path.join(
        container_rootfs("box"), "root", "moved.txt"
    )).read() == "moved"


def test_sync_with_delete_mirrors_directory(tmp_path, builders):
    builders.make_container("box")
    source = tmp_path / "sync"
    source.mkdir()
    (source / "keep").write_text("new")
    destination = os.path.join(container_rootfs("box"), "mirror")
    os.makedirs(destination)
    with open(os.path.join(destination, "keep"), "w") as fh:
        fh.write("old")
    with open(os.path.join(destination, "remove"), "w") as fh:
        fh.write("stale")

    result = ProotDistro().sync(
        source, "box:/mirror", checksum=True, delete=True
    )

    assert result.operation == "sync"
    assert open(os.path.join(destination, "keep")).read() == "new"
    assert not os.path.exists(os.path.join(destination, "remove"))


def test_backup_restore_roundtrip(tmp_path, builders, capsys):
    manifest = builders.simple_image_manifest("example/app:1")
    builders.make_container("archive-box", manifest=manifest, files={
        "var/data": b"payload",
    })
    archive = tmp_path / "archive-box.tar.gz"
    pd = ProotDistro()

    backup = pd.backup("archive-box", archive)

    assert backup == BackupResult(
        "archive-box", str(archive), "gzip", archive.stat().st_size
    )
    assert archive.is_file()

    pd.remove_container("archive-box")
    restored = pd.restore(archive)

    assert isinstance(restored, RestoreResult)
    assert restored.container.name == "archive-box"
    assert restored.container.image_reference == "example/app:1"
    assert restored.archive == str(archive)
    assert open(os.path.join(
        container_rootfs("archive-box"), "var", "data"
    ), "rb").read() == b"payload"
    assert capsys.readouterr() == ("", "")


def test_backup_refuses_existing_output(tmp_path, builders):
    builders.make_container("box")
    archive = tmp_path / "existing.tar"
    archive.write_bytes(b"keep")

    with pytest.raises(AlreadyExistsError) as exc:
        ProotDistro().backup("box", archive)

    assert exc.value.operation == "backup"
    assert archive.read_bytes() == b"keep"


def test_backup_unsupported_extension_is_typed_error(tmp_path, builders):
    builders.make_container("box")

    with pytest.raises(UnsupportedOperationError) as exc:
        ProotDistro().backup("box", tmp_path / "box.tar.lz4")

    assert exc.value.operation == "backup"


def test_restore_missing_archive_is_typed_error(tmp_path):
    with pytest.raises(NotFoundError) as exc:
        ProotDistro().restore(tmp_path / "missing.tar")
    assert exc.value.operation == "restore"


def test_restore_corrupt_archive_is_integrity_error(tmp_path):
    archive = tmp_path / "corrupt.tar"
    archive.write_bytes(b"not a tar archive")

    with pytest.raises(IntegrityError) as exc:
        ProotDistro().restore(archive)

    assert exc.value.operation == "restore"


def test_restore_busy_container_is_typed_error(tmp_path, builders):
    from proot_distro.locking import ContainerLock

    builders.make_container("restore-busy", files={"data": b"x"})
    archive = tmp_path / "restore-busy.tar"
    pd = ProotDistro()
    pd.backup("restore-busy", archive)
    lock = ContainerLock("restore-busy", exclusive=False, command="login")
    assert lock.acquire()
    try:
        with pytest.raises(BusyError) as exc:
            pd.restore(archive)
    finally:
        lock.release()
    assert exc.value.operation == "restore"


def test_transfer_missing_source_is_typed_error(tmp_path, builders):
    builders.make_container("box")

    with pytest.raises(NotFoundError) as exc:
        ProotDistro().copy(tmp_path / "missing", "box:/missing")

    assert exc.value.operation == "copy"


@pytest.mark.parametrize("method,args,kwargs,operation", [
    ("copy", ("", "box:/x"), {}, "copy"),
    ("copy", ("a", "box:/x"), {"recursive": 1}, "copy"),
    ("move", ("a\0b", "box:/x"), {}, "move"),
    ("sync", ("a", "box:/x"), {"checksum": "yes"}, "sync"),
    ("sync", ("a", "box:/x"), {"delete": 1}, "sync"),
    ("backup", ("bad/name", "out.tar"), {}, "backup"),
    ("backup", ("box", "out.tar"), {"compression": "zip"}, "backup"),
    ("restore", ("",), {}, "restore"),
])
def test_transfer_and_archive_validation_happens_before_worker(
        method, args, kwargs, operation, monkeypatch):
    monkeypatch.setattr(
        "proot_distro.api.program._run_worker",
        lambda *_args, **_kwargs: pytest.fail("worker should not start"),
    )

    with pytest.raises(InvalidArgumentError) as exc:
        getattr(ProotDistro(), method)(*args, **kwargs)

    assert exc.value.operation == operation
