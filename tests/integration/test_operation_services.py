import os

import pytest

from proot_distro.operation_errors import (
    AlreadyExistsFailure,
    NotFoundFailure,
    OperationFailure,
)
from proot_distro.operations import (
    CacheCleanup,
    ContainerRemoval,
    ContainerRename,
    ImageRemoval,
    OperationContext,
    clear_cache,
    remove_container,
    remove_image,
    rename_container,
)
from proot_distro.constants import BASE_CACHE_DIR
from proot_distro.helpers.docker.cache import (
    iter_cached_images, save_manifest_cache,
)
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA
from proot_distro.operations.errors import ImageNotFoundFailure
from proot_distro.paths import container_dir, container_rootfs


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
