import io
import os
import threading

import pytest

from proot_distro.api import (
    AlreadyExistsError,
    BackupResult,
    BusyError,
    EventKind,
    InvalidArgumentError,
    IntegrityError,
    NotFoundError,
    OperationCancelledError,
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
    assert any(
        event.kind == EventKind.PROGRESS
        and event.phase == "copy"
        and event.resource == "box:/tree"
        for event in events
    )
    assert open(os.path.join(container_rootfs("box"), "tree", "a")).read() == "a"


def test_live_event_handler_exception_stops_worker(
        tmp_path, builders):
    builders.make_container("event-box")
    source = tmp_path / "event-tree"
    source.mkdir()
    for index in range(100):
        (source / f"item-{index}").write_bytes(b"payload")
    expected = LookupError("event receiver unavailable")
    callback_threads = []
    caller_thread = threading.get_ident()

    def fail(event):
        callback_threads.append(threading.get_ident())
        if event.kind == EventKind.PROGRESS:
            raise expected

    with pytest.raises(LookupError) as exc:
        ProotDistro(event_handler=fail).copy(
            source, "event-box:/tree", recursive=True
        )

    assert exc.value is expected
    assert callback_threads and set(callback_threads) == {caller_thread}
    destination = os.path.join(container_rootfs("event-box"), "tree")
    assert len(os.listdir(destination)) < 100


def test_cancellation_interrupts_recursive_copy(tmp_path, builders):
    builders.make_container("cancel-copy")
    source = tmp_path / "cancel-source"
    source.mkdir()
    for index in range(1000):
        (source / f"item-{index}").write_bytes(b"payload")
    cancelled = threading.Event()

    def cancel_on_progress(event):
        if event.kind == EventKind.PROGRESS:
            cancelled.set()

    with pytest.raises(OperationCancelledError) as exc:
        ProotDistro(event_handler=cancel_on_progress).copy(
            source, "cancel-copy:/tree", recursive=True,
            cancel_event=cancelled,
        )

    assert exc.value.operation == "copy"
    assert cancelled.is_set()


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


def test_backup_restore_roundtrip_with_binary_streams(builders):
    builders.make_container("stream-box", files={
        "var/data": b"streamed payload",
    })
    archive = io.BytesIO()
    pd = ProotDistro()

    backup = pd.backup("stream-box", archive, compression="gzip")

    assert backup.output is None
    assert backup.compression == "gzip"
    assert backup.size_bytes == len(archive.getvalue())
    assert not archive.closed

    pd.remove_container("stream-box")
    archive.seek(0)
    restored = pd.restore(archive)

    assert restored.container.name == "stream-box"
    assert restored.archive is None
    assert not archive.closed
    assert open(os.path.join(
        container_rootfs("stream-box"), "var", "data"
    ), "rb").read() == b"streamed payload"


def test_archive_streams_are_sequential_and_support_short_writes(builders):
    builders.make_container("sequential-box", files={"payload": b"value"})

    class ShortWriter:
        def __init__(self):
            self.data = bytearray()

        def write(self, data):
            count = min(17, len(data))
            self.data.extend(data[:count])
            return count

    class ChunkReader:
        def __init__(self, data):
            self.data = data
            self.offset = 0

        def read(self, size=-1):
            if self.offset >= len(self.data):
                return b""
            size = min(size, 31)
            chunk = self.data[self.offset:self.offset + size]
            self.offset += len(chunk)
            return chunk

    output = ShortWriter()
    pd = ProotDistro()
    backup = pd.backup("sequential-box", output)
    assert backup.compression == "none"
    pd.remove_container("sequential-box")

    restored = pd.restore(ChunkReader(bytes(output.data)))

    assert restored.container.name == "sequential-box"
    assert open(os.path.join(
        container_rootfs("sequential-box"), "payload"
    ), "rb").read() == b"value"


def test_backup_stream_exception_propagates_from_caller(builders):
    builders.make_container("broken-output", files={"payload": b"value"})
    expected = LookupError("stream destination unavailable")

    class BrokenOutput:
        def write(self, _data):
            raise expected

    with pytest.raises(LookupError) as exc:
        ProotDistro().backup(
            "broken-output", BrokenOutput(), compression="none"
        )

    assert exc.value is expected


def test_restore_stream_exception_propagates_from_caller():
    expected = LookupError("stream source unavailable")

    class BrokenInput:
        def read(self, _size=-1):
            raise expected

    with pytest.raises(LookupError) as exc:
        ProotDistro().restore(BrokenInput())

    assert exc.value is expected


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
    ("backup", ("box", io.StringIO()), {}, "backup"),
    ("restore", ("",), {}, "restore"),
    ("restore", (io.StringIO(),), {}, "restore"),
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
