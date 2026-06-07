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
