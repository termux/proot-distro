import os
from pathlib import Path

import pytest

from proot_distro.api import (
    ContainerInfo,
    InvalidArgumentError,
    OperationError,
    ProotDistro,
    RuntimeConfig,
)
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA
from proot_distro.paths import container_dir, container_rootfs


def _rootfs_archive(tmp_path, builders, name="rootfs.tar.gz"):
    archive = tmp_path / name
    builders.make_tar(str(archive), builders.rootfs_members([
        {"name": "etc/os-release", "type": "file", "data": b"ID=api\n"},
    ]), compression="gz")
    return archive


def test_install_local_archive_returns_container_info(tmp_path, builders,
                                                      capsys):
    archive = _rootfs_archive(tmp_path, builders)

    result = ProotDistro().install(archive, name="api-box")

    assert result == ContainerInfo("api-box", None, None, None)
    assert os.path.isfile(os.path.join(container_rootfs("api-box"),
                                       "etc", "os-release"))
    assert capsys.readouterr() == ("", "")


def test_install_can_derive_local_archive_name(tmp_path, builders):
    archive = _rootfs_archive(tmp_path, builders, "derived-rootfs.tar.gz")
    result = ProotDistro().install(archive)
    assert result.name == "derived-rootfs"


def test_install_treats_relative_pathlike_as_local(tmp_path, builders,
                                                   monkeypatch):
    archive = _rootfs_archive(tmp_path, builders, "relative-rootfs.tar.gz")
    monkeypatch.chdir(tmp_path)

    result = ProotDistro().install(Path(archive.name))

    assert result.name == "relative-rootfs"


def test_configured_instances_have_independent_state(tmp_path, builders):
    archive = _rootfs_archive(tmp_path, builders, "isolated-rootfs.tar.gz")
    first = ProotDistro(RuntimeConfig(
        tmp_path / "first-state", tmp_path / "first-cache"
    ))
    second = ProotDistro(RuntimeConfig(
        tmp_path / "second-state", tmp_path / "second-cache"
    ))

    installed = first.install(archive, name="isolated")

    assert installed.name == "isolated"
    assert [item.name for item in first.list_containers()] == ["isolated"]
    assert second.list_containers() == ()
    assert os.path.isdir(os.path.join(
        first.config.runtime_dir, "containers", "isolated", "rootfs"
    ))

    removed = first.remove_container("isolated")
    assert removed.name == "isolated"
    assert first.list_containers() == ()


def test_install_failure_is_operation_error_without_output(tmp_path, capsys):
    with pytest.raises(OperationError) as exc:
        ProotDistro().install(tmp_path / "missing.tar", name="missing")

    assert exc.value.operation == "install"
    assert "does not exist" in str(exc.value)
    assert capsys.readouterr() == ("", "")


def test_remove_returns_former_metadata(builders, capsys):
    manifest = builders.simple_image_manifest("example/app:1", "x86_64")
    builders.make_container("box", manifest=manifest)

    removed = ProotDistro().remove_container("box")

    assert removed.image_reference == "example/app:1"
    assert not os.path.exists(container_dir("box"))
    assert capsys.readouterr() == ("", "")


def test_remove_missing_container_is_operation_error(capsys):
    with pytest.raises(OperationError) as exc:
        ProotDistro().remove_container("ghost")
    assert exc.value.operation == "remove_container"
    assert "is not installed" in str(exc.value)
    assert capsys.readouterr() == ("", "")


def test_rename_returns_new_container_info(builders, capsys):
    builders.make_container(
        "old", manifest=builders.simple_image_manifest("example/app:1")
    )

    renamed = ProotDistro().rename_container("old", "new")

    assert renamed.name == "new"
    assert renamed.image_reference == "example/app:1"
    assert not os.path.exists(container_dir("old"))
    assert os.path.isdir(container_rootfs("new"))
    assert capsys.readouterr() == ("", "")


def test_reset_rebuilds_from_cached_image(builders, capsys):
    architecture = "x86_64"
    digest, size, diff_id = builders.seed_cached_layer([
        {"name": "etc/hostname", "type": "file", "data": b"api-reset\n"},
    ])
    manifest = {
        "schemaVersion": 2,
        "layers": [{
            "digest": digest,
            "size": size,
            "mediaType": OCI_LAYER_MEDIA,
        }],
    }
    image_config = {"config": {}, "rootfs": {"diff_ids": [diff_id]}}
    save_manifest_cache(
        "api-reset:1", architecture, manifest, "library/api-reset",
        image_config,
    )
    builders.make_container("reset-box", manifest={
        "image_ref": "api-reset:1",
        "arch": architecture,
        "manifest": manifest,
        "image_config": image_config,
    })
    hostname = os.path.join(container_rootfs("reset-box"), "etc", "hostname")
    with open(hostname, "wb") as fh:
        fh.write(b"stale")

    result = ProotDistro().reset_container("reset-box")

    assert result.name == "reset-box"
    with open(hostname, "rb") as fh:
        assert fh.read() == b"api-reset\n"
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("method,args,operation", [
    ("remove_container", ("../bad",), "remove_container"),
    ("rename_container", ("good", "bad/name"), "rename_container"),
    ("reset_container", (None,), "reset_container"),
])
def test_container_methods_validate_names_before_worker(method, args, operation,
                                                        monkeypatch):
    monkeypatch.setattr(
        "proot_distro.api.program._run_worker",
        lambda *_a, **_k: pytest.fail("worker should not start"),
    )
    with pytest.raises(InvalidArgumentError) as exc:
        getattr(ProotDistro(), method)(*args)
    assert exc.value.operation == operation


def test_rename_requires_different_names(monkeypatch):
    monkeypatch.setattr(
        "proot_distro.api.program._run_worker",
        lambda *_a, **_k: pytest.fail("worker should not start"),
    )
    with pytest.raises(InvalidArgumentError):
        ProotDistro().rename_container("box", "box")


@pytest.mark.parametrize("source,kwargs", [
    (None, {}),
    ("", {}),
    ("image:1", {"name": "bad/name"}),
    ("image:1", {"architecture": "unknown"}),
    ("image:1", {"allow_insecure": 1}),
])
def test_install_validates_before_worker(source, kwargs, monkeypatch):
    monkeypatch.setattr(
        "proot_distro.api.program._run_worker",
        lambda *_a, **_k: pytest.fail("worker should not start"),
    )
    with pytest.raises(InvalidArgumentError) as exc:
        ProotDistro().install(source, **kwargs)
    assert exc.value.operation == "install"
