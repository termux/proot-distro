# Tests for proot_distro.helpers.docker.cache — the digest grammar choke
# point and the on-disk cache path/IO helpers.

import os

import pytest

from proot_distro.constants import LAYER_CACHE_DIR, MANIFEST_CACHE_DIR
from proot_distro.helpers.docker import cache


@pytest.mark.parametrize("digest", [
    "sha256:abcdef0123456789",
    "sha256:DEADBEEF",
    "sha512:0011223344556677",
    "sha256+b64:abcd",      # multi-component algorithm
    "multihash.v1:ff00",
])
def test_validate_digest_accepts(digest):
    assert cache.validate_digest(digest) == digest


@pytest.mark.parametrize("digest", [
    "../foo:bar",
    "sha256:",            # empty hex
    ":abcd",              # empty algo
    "sha256",             # no colon
    "sha256:xyz",         # non-hex
    "sha256:../../x",     # traversal in hex part
    "sha256:dead/beef",   # separator in hex
    "sha 256:abcd",       # space in algo
    "",
])
def test_validate_digest_rejects(digest):
    with pytest.raises(RuntimeError):
        cache.validate_digest(digest)


def test_validate_digest_rejects_non_str():
    with pytest.raises(RuntimeError):
        cache.validate_digest(None)


def test_layer_cache_path_under_cache_root_and_colon_mapped():
    p = cache.layer_cache_path("sha256:abc123")
    assert p == os.path.join(LAYER_CACHE_DIR, "sha256_abc123")
    assert os.path.abspath(p).startswith(os.path.abspath(LAYER_CACHE_DIR) + os.sep)


def test_layer_cache_path_rejects_traversal():
    with pytest.raises(RuntimeError):
        cache.layer_cache_path("../../etc/passwd:bar")


def test_manifest_cache_path_stable_and_arch_sensitive():
    a1 = cache.manifest_cache_path("ubuntu:24.04", "x86_64")
    a2 = cache.manifest_cache_path("ubuntu:24.04", "x86_64")
    b = cache.manifest_cache_path("ubuntu:24.04", "aarch64")
    assert a1 == a2
    assert a1 != b
    assert os.path.dirname(a1) == MANIFEST_CACHE_DIR
    assert a1.endswith(".json")


def test_manifest_cache_roundtrip():
    manifest = {"schemaVersion": 2, "layers": [{"digest": "sha256:aa"}]}
    image_config = {"config": {"Env": ["A=B"]}}
    cache.save_manifest_cache("ubuntu:24.04", "x86_64", manifest,
                              "library/ubuntu", image_config)
    m, repo, cfg = cache.load_manifest_cache("ubuntu:24.04", "x86_64")
    assert m == manifest
    assert repo == "library/ubuntu"
    assert cfg == image_config


def test_manifest_cache_miss_returns_none():
    m, repo, cfg = cache.load_manifest_cache("never:pulled", "x86_64")
    assert m is None
    assert repo is None
    assert cfg == {}


def test_all_layers_cached(builders):
    digest, _size, _diff = builders.seed_cached_layer(
        [{"name": "etc/x", "type": "file", "data": b"1"}]
    )
    assert cache.all_layers_cached([{"digest": digest}]) is True
    assert cache.all_layers_cached(
        [{"digest": "sha256:" + "0" * 64}]
    ) is False


def test_manifest_cache_records_ref_and_arch():
    import json
    cache.save_manifest_cache("ubuntu:24.04", "x86_64", {"layers": []},
                              "library/ubuntu", {})
    with open(cache.manifest_cache_path("ubuntu:24.04", "x86_64")) as fh:
        payload = json.load(fh)
    assert payload["image_ref"] == "ubuntu:24.04"
    assert payload["arch"] == "x86_64"


def test_manifest_cache_path_ignores_ref_spelling():
    a = cache.manifest_cache_path("ubuntu:24.04", "x86_64")
    b = cache.manifest_cache_path("docker.io/library/ubuntu:24.04", "x86_64")
    assert a == b


def test_annotate_manifest_cache_backfills_only_when_missing():
    import json
    path = cache.save_manifest_cache("img:1", "x86_64", {"layers": []},
                                     "library/img", {})
    with open(path) as fh:
        payload = json.load(fh)
    del payload["image_ref"], payload["arch"]
    with open(path, "w") as fh:
        json.dump(payload, fh)

    cache.annotate_manifest_cache("img:1", "x86_64")
    with open(path) as fh:
        assert json.load(fh)["image_ref"] == "img:1"

    # A miss must not create anything.
    cache.annotate_manifest_cache("never:pulled", "x86_64")
    assert not os.path.exists(
        cache.manifest_cache_path("never:pulled", "x86_64")
    )


def test_image_cache_entry_reads_back_a_record():
    cache.save_manifest_cache("img:1", "x86_64", {"layers": []},
                              "library/img", {})
    record = cache.image_cache_entry("img:1", "x86_64")
    assert record["image_ref"] == "img:1"
    assert record["arch"] == "x86_64"
    assert cache.image_cache_entry("img:1", "aarch64") is None


def test_iter_cached_images_skips_unreadable_entries():
    cache.save_manifest_cache("img:1", "x86_64", {"layers": []},
                              "library/img", {})
    with open(os.path.join(MANIFEST_CACHE_DIR, "garbage.json"), "w") as fh:
        fh.write("{not json")
    with open(os.path.join(MANIFEST_CACHE_DIR, "notjson.txt"), "w") as fh:
        fh.write("ignored")
    assert [r["image_ref"] for r in cache.iter_cached_images()] == ["img:1"]
