# Tests for proot_distro.helpers.oci_writer — manifest/config assembly,
# cache storage, and OCI image-layout tarball writing.

import hashlib
import json
import os
import tarfile

import pytest

from proot_distro.helpers import oci_writer
from proot_distro.helpers.docker.media import canonical_json
from proot_distro.helpers.docker.cache import load_manifest_cache


def test_build_manifest_and_config_digests():
    image_config = {"config": {"Env": ["A=B"]}, "history": [{"created": "x"}]}
    layers = [
        {"digest": "sha256:" + "a" * 64, "size": 10, "diff_id": "sha256:" + "b" * 64},
    ]
    manifest, config = oci_writer.build_manifest_and_config(
        image_config, layers, "amd64"
    )
    assert config["architecture"] == "amd64"
    assert config["os"] == "linux"
    assert config["rootfs"]["diff_ids"] == ["sha256:" + "b" * 64]
    # The manifest's config descriptor digest matches the canonical hash.
    expected = "sha256:" + hashlib.sha256(canonical_json(config)).hexdigest()
    assert manifest["config"]["digest"] == expected
    assert manifest["config"]["size"] == len(canonical_json(config))
    assert manifest["layers"][0]["digest"] == "sha256:" + "a" * 64
    assert manifest["schemaVersion"] == 2


def test_store_in_cache_is_loadable():
    image_config = {"config": {}, "history": []}
    layers = [{"digest": "sha256:" + "c" * 64, "size": 1, "diff_id": "sha256:" + "d" * 64}]
    manifest, config = oci_writer.build_manifest_and_config(
        image_config, layers, "amd64"
    )
    oci_writer.store_in_cache("myapp:1.0", "x86_64", manifest, config)
    m, repo, cfg = load_manifest_cache("myapp:1.0", "x86_64")
    assert m == manifest
    # Bare image names are normalised to the library/ namespace by parse_image_ref.
    assert repo == "library/myapp"
    assert cfg == config


def test_write_oci_archive_structure(tmp_path, builders):
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "etc/hostname", "type": "file", "data": b"guest\n"}]
    )
    image_config = {"config": {"Cmd": ["/bin/sh"]}, "history": [{"created": "x"}]}
    layers = [{"digest": digest, "size": size, "diff_id": diff_id}]
    manifest, config = oci_writer.build_manifest_and_config(
        image_config, layers, "amd64"
    )
    out = str(tmp_path / "img.oci.tar")
    oci_writer.write_oci_archive(out, manifest, config, "myapp:1.0")

    with tarfile.open(out, "r") as tf:
        names = tf.getnames()
        assert "oci-layout" in names
        assert "index.json" in names
        assert "manifest.json" in names  # docker-legacy
        # The layer blob is present under blobs/sha256/<hex>.
        hexd = digest.split(":", 1)[1]
        assert f"blobs/sha256/{hexd}" in names

        index = json.loads(tf.extractfile("index.json").read())
        man_digest = index["manifests"][0]["digest"]
        man_hex = man_digest.split(":", 1)[1]
        assert f"blobs/sha256/{man_hex}" in names

        docker_manifest = json.loads(tf.extractfile("manifest.json").read())
        assert docker_manifest[0]["RepoTags"] == ["myapp:1.0"]


def test_write_oci_archive_missing_blob_raises(tmp_path):
    # Manifest references a layer digest that is not in the cache.
    image_config = {"config": {}, "history": [{"created": "x"}]}
    layers = [{"digest": "sha256:" + "e" * 64, "size": 5, "diff_id": "sha256:" + "f" * 64}]
    manifest, config = oci_writer.build_manifest_and_config(
        image_config, layers, "amd64"
    )
    out = str(tmp_path / "img.oci.tar")
    with pytest.raises(RuntimeError):
        oci_writer.write_oci_archive(out, manifest, config, "x:1")
