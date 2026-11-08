import io
import json
import os
import tarfile

import pytest

from proot_distro.api import (
    AlreadyExistsError,
    BuildResult,
    CacheCleanupResult,
    BusyError,
    EventKind,
    ImageRemovalResult,
    IntegrityError,
    InvalidArgumentError,
    NotFoundError,
    ProotDistro,
    RuntimeConfig,
)
from proot_distro.arch import get_device_cpu_arch
from proot_distro.helpers.docker.cache import layer_cache_path
from proot_distro.helpers.docker.cache import manifest_cache_path
from proot_distro.constants import MANIFEST_CACHE_DIR
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


def test_build_accepts_dockerfile_and_oci_output_streams(tmp_path):
    context = tmp_path / "stream-context"
    (context / "rootfs").mkdir(parents=True)
    (context / "rootfs" / "payload").write_bytes(b"stream build")
    dockerfile = io.BytesIO(b"FROM scratch\nCOPY rootfs/ /\n")
    archive = io.BytesIO()

    result = ProotDistro().build(
        context,
        dockerfile=dockerfile,
        tags=["stream-build:1"],
        outputs=archive,
    )

    assert result.outputs == (None,)
    assert not dockerfile.closed
    assert not archive.closed
    archive.seek(0)
    with tarfile.open(fileobj=archive, mode="r:") as image:
        names = image.getnames()
    assert names[0] == "oci-layout"
    assert "index.json" in names


def test_build_supports_mixed_path_and_stream_outputs(tmp_path):
    context = _context(tmp_path)
    path_output = tmp_path / "mixed.oci.tar"
    stream_output = io.BytesIO()

    result = ProotDistro().build(
        context,
        tags=["mixed-output:1"],
        outputs=[path_output, stream_output],
    )

    assert result.outputs == (str(path_output), None)
    assert path_output.is_file()
    assert stream_output.getvalue().startswith(b"oci-layout")


def test_build_run_output_streams_as_live_events(tmp_path):
    context = tmp_path / "run-output-context"
    context.mkdir()
    (context / "Dockerfile").write_text(
        "FROM scratch\nRUN echo output-from-step\n"
    )
    fake_proot = tmp_path / "fake-build-proot"
    fake_proot.write_text(
        "#!/bin/sh\nprintf 'streamed RUN output\\n'\n"
    )
    fake_proot.chmod(0o755)
    default = RuntimeConfig.default()
    events = []
    pd = ProotDistro(RuntimeConfig(
        default.runtime_dir, default.cache_dir, fake_proot
    ), event_handler=events.append)

    pd.build(context, tags=["event-output:1"])

    assert any(
        event.operation == "build"
        and event.phase == "build-output"
        and "streamed RUN output" in event.message
        for event in events
    )


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


def test_push_corrupt_config_is_integrity_error(tmp_path):
    context = _context(tmp_path)
    pd = ProotDistro()
    pd.build(context, tags=["integrity-api:1"])
    architecture = get_device_cpu_arch()
    path = manifest_cache_path("integrity-api:1", architecture)
    with open(path) as fh:
        cached = json.load(fh)
    cached["image_config"]["config"]["Env"] = ["CHANGED=1"]
    with open(path, "w") as fh:
        json.dump(cached, fh)

    with pytest.raises(IntegrityError) as exc:
        pd.push("integrity-api:1", architecture=architecture)

    assert exc.value.operation == "push"


def test_orphan_cleanup_busy_lock_is_typed_error(builders):
    from proot_distro.locking import BuildLock

    lock = BuildLock("busy-cache:1", "x86_64", command="build")
    assert lock.acquire()
    try:
        with pytest.raises(BusyError) as exc:
            ProotDistro().clear_cache(orphan=True)
    finally:
        lock.release()

    assert exc.value.operation == "clear_cache"


def test_orphan_cleanup_unreadable_manifest_is_integrity_error():
    path = os.path.join(MANIFEST_CACHE_DIR, "broken.json")
    with open(path, "w") as fh:
        fh.write("not json")

    with pytest.raises(IntegrityError) as exc:
        ProotDistro().clear_cache(orphan=True)

    assert exc.value.operation == "clear_cache"


@pytest.mark.parametrize("method,args,kwargs,operation", [
    ("build", ("",), {}, "build"),
    ("build", ("context",), {"tags": ["BAD TAG"]}, "build"),
    ("build", ("context",), {"build_args": []}, "build"),
    ("build", ("context",), {"no_cache": 1}, "build"),
    ("build", ("context",), {"dockerfile": io.StringIO()}, "build"),
    ("build", ("context",), {"outputs": [io.StringIO()]}, "build"),
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
