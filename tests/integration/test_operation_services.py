import os

import pytest

import proot_distro.operations.inventory as inventory
import proot_distro.operations.backup as backup_service
from proot_distro.operation_errors import (
    AlreadyExistsFailure,
    NotFoundFailure,
    OperationFailure,
)
from proot_distro.operations import (
    ActiveSession,
    ArchiveBackup,
    ArchiveRestore,
    CacheCleanup,
    CachedImage,
    ContainerRemoval,
    ContainerRename,
    ImageRemoval,
    InstalledContainer,
    OperationContext,
    Transfer,
    backup_container,
    clear_cache,
    copy_path,
    list_containers,
    list_images,
    list_sessions,
    move_path,
    remove_container,
    remove_image,
    rename_container,
    restore_container,
    sync_path,
)
from proot_distro.constants import BASE_CACHE_DIR
from proot_distro.helpers.docker.cache import (
    iter_cached_images, save_manifest_cache,
)
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA
from proot_distro.operations.errors import ImageNotFoundFailure
from proot_distro.paths import container_dir, container_rootfs


def test_container_inventory_service_returns_recorded_origin(builders):
    manifest = builders.simple_image_manifest("service/app:1", "aarch64")
    digest = "sha256:" + "ab" * 32
    manifest["manifest"]["config"] = {"digest": digest}
    builders.make_container("service-inventory", manifest=manifest)

    assert list_containers() == (
        InstalledContainer(
            "service-inventory", "service/app:1", "aarch64", digest
        ),
    )


def test_session_inventory_service_copies_registry_command(monkeypatch):
    command = ["/bin/sh", "-l"]
    monkeypatch.setattr(inventory, "active_sessions", lambda: [{
        "pid": 123,
        "container": "service-session",
        "kind": "login",
        "command": command,
        "user": "root",
        "start_time": 10.5,
        "isolated": True,
        "minimal": False,
        "detach": True,
    }])

    result = list_sessions()
    command.append("changed")

    assert result == (
        ActiveSession(
            123, "service-session", "login", ("/bin/sh", "-l"),
            "root", 10.5, True, False, True,
        ),
    )


def test_transfer_services_return_results_and_contextual_events(
    tmp_path, builders,
):
    builders.make_container("service-transfer")
    source = tmp_path / "copy-source"
    source.write_text("copy payload")
    events = []
    context = OperationContext(
        emit=lambda kind, message, phase, resource: events.append(
            (kind, message, phase, resource)
        )
    )

    copied = copy_path(
        str(source), "service-transfer:/copied", context=context
    )
    moved = move_path(
        "service-transfer:/copied", str(tmp_path / "moved"),
        context=context,
    )

    assert copied == Transfer(
        "copy", str(source), "service-transfer:/copied", 0
    )
    assert moved == Transfer(
        "move", "service-transfer:/copied", str(tmp_path / "moved"), 0
    )
    assert (tmp_path / "moved").read_text() == "copy payload"
    assert not os.path.lexists(
        os.path.join(container_rootfs("service-transfer"), "copied")
    )
    assert {phase for _kind, _message, phase, _resource in events} == {
        "copy", "move",
    }


def test_sync_service_mirrors_directory(tmp_path, builders):
    builders.make_container("service-sync")
    source = tmp_path / "sync-source"
    source.mkdir()
    (source / "payload").write_text("synchronized")

    result = sync_path(str(source), "service-sync:/mirror")

    assert result == Transfer(
        "sync", str(source), "service-sync:/mirror", 0
    )
    with open(os.path.join(
        container_rootfs("service-sync"), "mirror", "payload"
    )) as fh:
        assert fh.read() == "synchronized"


def test_archive_services_return_results_and_contextual_events(
    tmp_path, builders,
):
    builders.make_container("service-archive", files={
        "var/payload": b"archived",
    })
    archive = tmp_path / "service-archive.tar.gz"
    events = []
    context = OperationContext(
        emit=lambda kind, message, phase, resource: events.append(
            (kind, message, phase, resource)
        )
    )

    backup = backup_container(
        "service-archive", output_path=str(archive), context=context
    )
    remove_container("service-archive")
    restored = restore_container(
        archive_path=str(archive), context=context
    )

    assert backup == ArchiveBackup(
        "service-archive", str(archive), "gzip", archive.stat().st_size
    )
    assert restored == ArchiveRestore("service-archive", str(archive))
    with open(os.path.join(
        container_rootfs("service-archive"), "var", "payload"
    ), "rb") as fh:
        assert fh.read() == b"archived"
    assert {phase for _kind, _message, phase, _resource in events} == {
        "archive", "extract",
    }


def test_backup_path_is_not_published_after_archive_failure(
    tmp_path, builders, monkeypatch,
):
    builders.make_container("service-broken-backup", files={
        "payload": b"data",
    })
    archive = tmp_path / "broken.tar"
    real_each_entry = backup_service._each_entry
    calls = []

    def fail_archive(*args, **kwargs):
        calls.append(None)
        if len(calls) == 2:
            raise OSError("archive write failed")
        return real_each_entry(*args, **kwargs)

    monkeypatch.setattr(backup_service, "_each_entry", fail_archive)

    with pytest.raises(OperationFailure):
        backup_container(
            "service-broken-backup", output_path=str(archive)
        )

    assert not archive.exists()
    assert not list(tmp_path.glob("*.pd-backup"))


def test_remove_service_returns_result_and_structured_events(builders):
    builders.make_container("service-remove")
    events = []
    context = OperationContext(
        emit=lambda kind, message, phase, resource: events.append(
            (kind, message, phase, resource)
        )
    )

    result = remove_container("service-remove", context=context)

    assert result == ContainerRemoval("service-remove", True)
    assert not os.path.lexists(container_dir("service-remove"))
    assert events
    assert all(
        phase == "remove" and resource == "service-remove"
        for _kind, _message, phase, resource in events
    )


def test_remove_service_raises_typed_missing_failure():
    with pytest.raises(NotFoundFailure):
        remove_container("service-missing")


def test_rename_service_returns_result_and_structured_events(builders):
    builders.make_container("service-old")
    events = []
    context = OperationContext(
        emit=lambda kind, message, phase, resource: events.append(
            (kind, message, phase, resource)
        )
    )

    result = rename_container(
        "service-old", "service-new", context=context
    )

    assert result == ContainerRename("service-old", "service-new")
    assert not os.path.lexists(container_dir("service-old"))
    assert os.path.isdir(container_rootfs("service-new"))
    assert events
    assert all(
        phase == "rename" and resource == "service-new"
        for _kind, _message, phase, resource in events
    )


def test_rename_service_raises_typed_existing_failure(builders):
    builders.make_container("service-source")
    builders.make_container("service-target")

    with pytest.raises(AlreadyExistsFailure):
        rename_container("service-source", "service-target")


def test_rename_service_refuses_planted_container_entry(tmp_path):
    outside = tmp_path / "outside-service"
    outside.mkdir()
    keepsake = outside / "keepsake"
    keepsake.write_text("host data")
    os.symlink(outside, container_dir("service-planted"))

    with pytest.raises(OperationFailure) as exc:
        rename_container("service-planted", "service-safe")

    assert "not usable" in str(exc.value)
    assert keepsake.read_text() == "host data"
    assert os.path.islink(container_dir("service-planted"))
    assert not os.path.lexists(container_dir("service-safe"))


def test_cache_service_returns_counts_and_structured_events():
    os.makedirs(BASE_CACHE_DIR, exist_ok=True)
    payload = os.path.join(BASE_CACHE_DIR, "service-cache-data")
    with open(payload, "wb") as fh:
        fh.write(b"cache payload")
    events = []
    context = OperationContext(
        emit=lambda kind, message, phase, resource: events.append(
            (kind, message, phase, resource)
        )
    )

    result = clear_cache(context=context)

    assert result == CacheCleanup("all", len(b"cache payload"), 0)
    assert events
    assert all(
        phase == "clear-cache" and resource == "all"
        for _kind, _message, phase, resource in events
    )


def test_image_removal_service_returns_records_and_events(builders):
    digest, size, _diff = builders.seed_cached_layer([
        {"name": "etc/service-image", "type": "file", "data": b"image"},
    ])
    save_manifest_cache(
        "service-image:1", "x86_64", {
            "schemaVersion": 2,
            "layers": [{
                "digest": digest,
                "size": size,
                "mediaType": OCI_LAYER_MEDIA,
            }],
        }, "library/service-image", {},
    )
    events = []
    context = OperationContext(
        emit=lambda kind, message, phase, resource: events.append(
            (kind, message, phase, resource)
        )
    )

    images = list_images()
    assert len(images) == 1
    assert isinstance(images[0], CachedImage)
    assert images[0].reference == "service-image:1"

    result = remove_image("service-image:1", context=context)

    assert isinstance(result, ImageRemoval)
    assert [item.reference for item in result.removed] == ["service-image:1"]
    assert result.reclaimed_bytes > 0
    assert iter_cached_images() == []
    assert events
    assert all(
        phase == "remove-image"
        for _kind, _message, phase, _resource in events
    )


def test_image_removal_service_raises_typed_missing_failure():
    with pytest.raises(ImageNotFoundFailure) as exc:
        remove_image("service-missing:1")

    assert exc.value.identifier == "service-missing:1"
