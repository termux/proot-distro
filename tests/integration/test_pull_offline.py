# Integration tests for the fully-offline branch of pull_image (manifest +
# all layers cached -> no network), plus the no-network and zstd failure paths.

import os

import urllib.error

import pytest

from proot_distro.helpers.docker import pull as pull_mod
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA


def _seed_manifest(builders, image_ref, arch, members, media=OCI_LAYER_MEDIA):
    digest, size, diff_id = builders.seed_cached_layer(members)
    manifest = {
        "schemaVersion": 2,
        "layers": [{"digest": digest, "size": size, "mediaType": media}],
    }
    image_config = {"config": {"Env": ["X=1"]}, "rootfs": {"diff_ids": [diff_id]}}
    save_manifest_cache(image_ref, arch, manifest, "library/x", image_config)
    return digest


def test_pull_fully_offline(tmp_path, builders):
    _seed_manifest(builders, "x:1", "x86_64", [
        {"name": "etc/hostname", "type": "file", "data": b"cached\n"},
    ])
    root = tmp_path / "rootfs"
    root.mkdir()
    meta = pull_mod.pull_image("x:1", str(root), "x86_64")
    assert open(os.path.join(str(root), "etc", "hostname"), "rb").read() == b"cached\n"
    assert meta["manifest"]["layers"]
    assert meta["image_config"]["config"]["Env"] == ["X=1"]


def test_pull_missing_layer_no_network(tmp_path, builders, monkeypatch):
    # Manifest cached but the referenced layer blob is absent, and the network
    # is unavailable -> RuntimeError (no traceback escaping).
    manifest = {
        "schemaVersion": 2,
        "layers": [{"digest": "sha256:" + "0" * 64, "size": 10,
                    "mediaType": OCI_LAYER_MEDIA}],
    }
    save_manifest_cache("x:miss", "x86_64", manifest, "library/x", {"config": {}})

    def _no_net(*a, **k):
        raise urllib.error.URLError("offline")

    monkeypatch.setattr(pull_mod, "get_auth_token", _no_net)
    root = tmp_path / "rootfs"
    root.mkdir()
    with pytest.raises(RuntimeError):
        pull_mod.pull_image("x:miss", str(root), "x86_64")


def test_pull_zstd_layer_rejected(tmp_path, builders):
    # zstd-compressed layers are unsupported by Python's tarfile.
    _seed_manifest(
        builders, "x:zstd", "x86_64",
        [{"name": "etc/x", "type": "file", "data": b"z"}],
        media="application/vnd.oci.image.layer.v1.tar+zstd",
    )
    root = tmp_path / "rootfs"
    root.mkdir()
    with pytest.raises(RuntimeError) as exc:
        pull_mod.pull_image("x:zstd", str(root), "x86_64")
    assert "zstd" in str(exc.value)
