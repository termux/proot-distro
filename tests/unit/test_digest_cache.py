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


# ---------------------------------------------------------------------------
# Malformed cache entries — the manifest cache is a file this program
# wrote but does not own: on Termux BASE_CACHE_DIR sits under the
# $TERMUX_PREFIX bound read-write into every non-isolated container, so
# what an entry *says* is a guest's to choose. Every one of these used
# to end `list --image`, `remove --image` or `clear-cache --orphan` in a
# TypeError or an AttributeError, which no command handler catches.
# ---------------------------------------------------------------------------

_GOOD_DIGEST = "sha256:" + "ab" * 32


def _put_entry(payload, key="f" * 16):
    """Write a raw manifest-cache entry, bypassing save_manifest_cache."""
    import json
    os.makedirs(MANIFEST_CACHE_DIR, exist_ok=True)
    path = os.path.join(MANIFEST_CACHE_DIR, key + ".json")
    with open(path, "w") as fh:
        json.dump(payload, fh)
    return path


_MALFORMED = [
    ("layers is not a list", {"manifest": {"layers": 1}}),
    ("layers holds a number", {"manifest": {"layers": [7]}}),
    ("a layer names no digest", {"manifest": {"layers": [{"size": 1}]}}),
    ("a layer digest is a number",
     {"manifest": {"layers": [{"digest": 123}]}}),
    ("config is not an object",
     {"manifest": {"layers": [], "config": "bad"}}),
    ("config digest is a number",
     {"manifest": {"layers": [], "config": {"digest": 123}}}),
    ("manifest is not an object", {"manifest": "nope"}),
    ("manifest is absent", {"image_ref": "x:1"}),
]


@pytest.mark.parametrize("label,payload", _MALFORMED,
                         ids=[c[0] for c in _MALFORMED])
def test_iter_cached_images_skips_a_malformed_manifest(label, payload):
    cache.save_manifest_cache("img:1", "x86_64", {"layers": []},
                              "library/img", {})
    _put_entry(payload)
    assert [r["image_ref"] for r in cache.iter_cached_images()] == ["img:1"]


@pytest.mark.parametrize("label,payload", _MALFORMED,
                         ids=[c[0] for c in _MALFORMED])
def test_referenced_blob_digests_reports_a_malformed_manifest(label, payload):
    # An entry a sweep cannot enumerate is not an entry with no
    # references: clear-cache --orphan reads this and refuses to delete
    # anything while `unreadable` is non-empty.
    cache.save_manifest_cache(
        "img:1", "x86_64",
        {"layers": [{"digest": _GOOD_DIGEST}]}, "library/img", {},
    )
    path = _put_entry(payload)
    digests, unreadable = cache.referenced_blob_digests()
    assert unreadable == [path]
    assert digests == {_GOOD_DIGEST}


def test_a_config_descriptor_may_be_absent():
    # Locally built entries carry no config descriptor at all, and
    # save_manifest_cache has always stored the manifest verbatim.
    cache.save_manifest_cache("img:1", "x86_64", {"layers": []},
                              "library/img", {})
    record = cache.image_cache_entry("img:1", "x86_64")
    assert record is not None
    assert record["image_id"] == ""
    assert cache.referenced_blob_digests() == (set(), [])


def test_record_string_fields_survive_an_entry_that_lies_about_them():
    # The record's consumers index it: list --image measures each cell
    # with len() and pads it with ljust(), remove --image splits the
    # reference into registry/repo/tag, and iter_cached_images() sorts
    # on the reference and the architecture.
    _put_entry({
        "image_ref": {"not": "a string"},
        "repo": 5,
        "arch": ["x86_64"],
        "manifest": {"layers": [], "config": {"digest": _GOOD_DIGEST}},
        "image_config": {"architecture": [], "created": 5},
    })
    records = cache.iter_cached_images()
    assert len(records) == 1
    record = records[0]
    for field in ("image_ref", "repo", "arch", "image_id", "created"):
        assert isinstance(record[field], str), field
    assert record["image_ref"] == ""
    assert record["arch"] == ""
    assert record["created"] == ""
    assert record["image_id"] == "ab" * 32


# ---------------------------------------------------------------------------
# Blob integrity — the digest checks every cached-blob consumer runs
# ---------------------------------------------------------------------------

def test_split_digest_normalizes():
    assert cache.split_digest("SHA256:ABCD") == ("sha256", "abcd")


@pytest.mark.parametrize("digest", [
    "sha512:" + "0" * 128,      # well-formed but not hashable here
    "multihash.v1:ff00",
    "../foo:bar",               # malformed digests still raise
    "sha256:zz",
])
def test_split_digest_rejects_unhashable(digest):
    # A digest we cannot compute is a digest we cannot check, so it may
    # never reach a blob consumer as if it had been verified.
    with pytest.raises(RuntimeError):
        cache.split_digest(digest)


def test_data_matches_digest():
    import hashlib
    data = b"payload"
    good = "sha256:" + hashlib.sha256(data).hexdigest()
    assert cache.data_matches_digest(data, good) is True
    assert cache.data_matches_digest(b"other", good) is False
    assert cache.require_data_digest(data, good) == data
    with pytest.raises(RuntimeError, match="does not match its digest"):
        cache.require_data_digest(b"other", good, what="Manifest")


def _read_fd(fd):
    """Read a descriptor from the start without consuming ownership."""
    os.lseek(fd, 0, os.SEEK_SET)
    chunks = []
    while True:
        chunk = os.read(fd, 65536)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def test_open_verified_layer_accepts_intact_blob(builders):
    digest, _size, _diff = builders.seed_cached_layer(
        [{"name": "etc/x", "type": "file", "data": b"1"}]
    )
    fd = cache.open_verified_layer(digest)
    assert fd is not None
    try:
        # The descriptor is the blob, and it comes back rewound so the
        # caller can hand it straight to whoever reads it.
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
        assert _read_fd(fd) == open(cache.layer_cache_path(digest), "rb").read()
    finally:
        os.close(fd)


def test_open_verified_layer_reads_the_bytes_it_hashed(builders):
    # The point of returning a descriptor: repointing the *name* after the
    # check cannot change what the caller goes on to read.
    digest, _size, _diff = builders.seed_cached_layer(
        [{"name": "etc/x", "type": "file", "data": b"1"}]
    )
    path = cache.layer_cache_path(digest)
    good = open(path, "rb").read()

    fd = cache.open_verified_layer(digest)
    assert fd is not None
    try:
        os.unlink(path)
        with open(path, "wb") as fh:
            fh.write(b"swapped after the check")
        assert _read_fd(fd) == good
    finally:
        os.close(fd)


def test_open_verified_layer_evicts_and_warns(builders, capsys):
    digest, _size, _diff = builders.seed_cached_layer(
        [{"name": "etc/x", "type": "file", "data": b"1"}]
    )
    path = cache.layer_cache_path(digest)
    with open(path, "wb") as fh:
        fh.write(b"not the layer this name promises")

    assert cache.open_verified_layer(digest) is None
    assert not os.path.exists(path), "a mismatched blob is dropped"
    assert "does not match its digest" in capsys.readouterr().err


def test_open_verified_layer_keeps_blob_when_evict_false(builders):
    digest, _size, _diff = builders.seed_cached_layer(
        [{"name": "etc/x", "type": "file", "data": b"1"}]
    )
    path = cache.layer_cache_path(digest)
    with open(path, "wb") as fh:
        fh.write(b"tampered")
    assert cache.open_verified_layer(digest, evict=False) is None
    assert os.path.isfile(path)


def test_open_verified_layer_missing_blob():
    assert cache.open_verified_layer("sha256:" + "0" * 64) is None


def test_open_verified_layer_refuses_a_symlinked_blob(builders, tmp_path):
    # The cache directory is not only ours to write (on Termux it sits
    # under the bound $TERMUX_PREFIX), so the entry at a blob's name may
    # be a link someone else put there. O_NOFOLLOW refuses it.
    digest, _size, _diff = builders.seed_cached_layer(
        [{"name": "etc/x", "type": "file", "data": b"1"}]
    )
    path = cache.layer_cache_path(digest)
    elsewhere = tmp_path / "elsewhere"
    os.replace(path, str(elsewhere))
    os.symlink(str(elsewhere), path)

    assert cache.open_verified_layer(digest, evict=False) is None


def test_open_required_layer_refuses_without_deleting(builders):
    digest, _size, _diff = builders.seed_cached_layer(
        [{"name": "etc/x", "type": "file", "data": b"1"}]
    )
    fd = cache.open_required_layer(digest)
    try:
        assert os.fstat(fd).st_size == os.path.getsize(
            cache.layer_cache_path(digest))
    finally:
        os.close(fd)

    path = cache.layer_cache_path(digest)
    with open(path, "wb") as fh:
        fh.write(b"tampered")
    with pytest.raises(RuntimeError, match="does not match its digest"):
        cache.open_required_layer(digest)
    # Locally built layers exist nowhere else: report, don't destroy.
    assert os.path.isfile(path)

    with pytest.raises(RuntimeError, match="missing"):
        cache.open_required_layer("sha256:" + "0" * 64)
