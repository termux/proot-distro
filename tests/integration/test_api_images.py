import os

import pytest

from proot_distro.api import (
    AlreadyExistsError,
    BuildResult,
    CacheCleanupResult,
    EventKind,
    ImageRemovalResult,
    InvalidArgumentError,
    NotFoundError,
    ProotDistro,
)
from proot_distro.helpers.docker.cache import layer_cache_path
from proot_distro.paths import container_rootfs


def _context(tmp_path):
    context = tmp_path / "context"
    (context / "rootfs" / "etc").mkdir(parents=True)
    (context / "rootfs" / "etc" / "hostname").write_text("api-build\n")
    (context / "Dockerfile").write_text(
        "FROM scratch\n"
        "COPY rootfs/ /\n"
        "ARG FLAVOR\n"
        "ENV FLAVOR=${FLAVOR}\n"
        'CMD ["/bin/sh"]\n'
    )
    return context


def test_build_returns_images_outputs_and_installed_container(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "api-image.oci.tar"
    events = []

    result = ProotDistro(event_handler=events.append).build(
        context,
        tags=["api-image:1"],
        build_args={"FLAVOR": "test"},
        outputs=[output],
        install_as="api-built",
    )

    assert isinstance(result, BuildResult)
    assert [image.reference for image in result.images] == ["api-image:1"]
    assert result.outputs == (str(output),)
    assert output.is_file()
    assert result.installed_container.name == "api-built"
    assert open(os.path.join(
        container_rootfs("api-built"), "etc", "hostname"
    )).read() == "api-build\n"
    assert any(event.kind == EventKind.INFO for event in events)


def test_remove_image_returns_removed_variants(tmp_path):
    context = _context(tmp_path)
    pd = ProotDistro()
    pd.build(context, tags=["remove-api:1"])

    result = pd.remove_image("remove-api:1")

    assert isinstance(result, ImageRemovalResult)
    assert [image.reference for image in result.removed] == ["remove-api:1"]
    assert all(image.reference != "remove-api:1" for image in pd.list_images())


def test_clear_cache_reports_actual_regular_file_usage(tmp_path):
    context = _context(tmp_path)
    pd = ProotDistro()
    pd.build(context, tags=["clear-api:1"])

    result = pd.clear_cache()

    assert isinstance(result, CacheCleanupResult)
    assert result.mode == "all"
    assert result.bytes_before > 0
    assert result.bytes_after == 0
    assert result.bytes_reclaimed == result.bytes_before
    assert pd.list_images() == ()


def test_clear_orphan_removes_unreferenced_layer(builders):
    digest, size, _diff = builders.seed_cached_layer([
        {"name": "orphan", "type": "file", "data": b"unused"},
    ])

    result = ProotDistro().clear_cache(orphan=True)

    assert result.mode == "orphan"
    assert result.bytes_reclaimed >= size
    assert not os.path.exists(layer_cache_path(digest))


def test_missing_cached_images_raise_not_found():
    pd = ProotDistro()

    with pytest.raises(NotFoundError) as remove_exc:
        pd.remove_image("missing:1")
    assert remove_exc.value.operation == "remove_image"

    with pytest.raises(NotFoundError) as push_exc:
        pd.push("missing:1")
    assert push_exc.value.operation == "push"


def test_build_refuses_existing_output_with_typed_error(tmp_path):
    context = _context(tmp_path)
    output = tmp_path / "existing.oci.tar"
    output.write_bytes(b"keep")

    with pytest.raises(AlreadyExistsError) as exc:
        ProotDistro().build(
            context, tags=["existing-output:1"], outputs=[output]
        )

    assert exc.value.operation == "build"
    assert output.read_bytes() == b"keep"


@pytest.mark.parametrize("method,args,kwargs,operation", [
    ("build", ("",), {}, "build"),
    ("build", ("context",), {"tags": ["BAD TAG"]}, "build"),
    ("build", ("context",), {"build_args": []}, "build"),
    ("build", ("context",), {"no_cache": 1}, "build"),
    ("push", ("",), {}, "push"),
    ("push", ("image@sha256:" + "a" * 64,), {}, "push"),
    ("push", ("image:1",), {"architecture": "unknown"}, "push"),
    ("remove_image", ("",), {}, "remove_image"),
    ("remove_image", ("image:1",), {"architecture": "unknown"},
     "remove_image"),
    ("clear_cache", (), {"orphan": 1}, "clear_cache"),
])
def test_image_api_validation_happens_before_worker(
        method, args, kwargs, operation, monkeypatch):
    monkeypatch.setattr(
        "proot_distro.api.program._run_worker",
        lambda *_args, **_kwargs: pytest.fail("worker should not start"),
    )

    with pytest.raises(InvalidArgumentError) as exc:
        getattr(ProotDistro(), method)(*args, **kwargs)

    assert exc.value.operation == operation
