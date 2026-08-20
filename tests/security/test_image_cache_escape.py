# Containment tests for the manifest cache — the inventory `list --image`
# prints, `remove --image` deletes from, and `clear-cache --orphan`
# computes its keep set from.
#
# Both cache directories sit below BASE_CACHE_DIR, which on Termux is
# under the $TERMUX_PREFIX bound read-write into every non-isolated
# container, so `oci_manifests` (or `cache` one level above it) is a name
# a guest can leave behind as a symlink. Every read went through
# os.listdir()/open() and every deletion through os.remove(), all of
# which follow one: with `oci_manifests -> <host dir>` planted, resolving
# an image read the JSON out of that directory and the removal unlinked
# the file it found there.

import hashlib
import json
import os
import shutil
from types import SimpleNamespace

import pytest

from proot_distro.commands.clear_cache import command_clear_cache
from proot_distro.commands.list import command_list
from proot_distro.commands.remove import command_remove
from proot_distro.constants import LAYER_CACHE_DIR, MANIFEST_CACHE_DIR
from proot_distro.helpers.docker import cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA, canonical_json


def _seed(builders, ref, arch="x86_64"):
    """Cache one image and return its single layer digest."""
    digest, size, _diff = builders.seed_cached_layer(
        [{"name": "etc/x", "type": "file", "data": b"payload"}]
    )
    config = {"architecture": "amd64", "os": "linux", "config": {}}
    config_bytes = canonical_json(config)
    manifest = {
        "schemaVersion": 2,
        "config": {
            "digest": "sha256:" + hashlib.sha256(config_bytes).hexdigest(),
            "size": len(config_bytes),
        },
        "layers": [{"digest": digest, "size": size,
                    "mediaType": OCI_LAYER_MEDIA}],
    }
    cache.save_manifest_cache(ref, arch, manifest, ref.split(":")[0], config)
    return digest


@pytest.fixture
def hijacked(tmp_path, builders):
    """Seed an image, then move the manifest cache out and link to it.

    What the guest gets to choose is where `oci_manifests` leads; the
    entries in the host directory it leads to are ordinary files that
    happen to parse.
    """
    digest = _seed(builders, "victim:latest")
    host_dir = tmp_path / "host-dir"
    shutil.move(MANIFEST_CACHE_DIR, str(host_dir))
    (host_dir / "keepme.json").write_text("not a cache entry\n")
    os.symlink(str(host_dir), MANIFEST_CACHE_DIR)
    return host_dir, digest


def _remove_image(target):
    command_remove(SimpleNamespace(
        target=target, image=True, override_arch=None, verbose=False,
    ))


def test_inventory_does_not_read_through_a_symlinked_cache(hijacked):
    assert cache.iter_cached_images() == []
    assert cache.image_cache_entry("victim:latest", "x86_64") is None
    assert cache.load_manifest_cache("victim:latest", "x86_64")[0] is None


def test_remove_image_does_not_delete_through_a_symlinked_cache(hijacked,
                                                                capsys):
    host_dir, _digest = hijacked
    before = sorted(os.listdir(str(host_dir)))

    with pytest.raises(SystemExit) as exc:
        _remove_image("victim:latest")
    assert exc.value.code == 1

    assert sorted(os.listdir(str(host_dir))) == before


def test_orphan_sweep_refuses_an_unreadable_manifest_cache(hijacked):
    # An unreadable set of references is not an absence of references:
    # treating it as one would collect every blob still in use.
    _host_dir, digest = hijacked
    with pytest.raises(SystemExit) as exc:
        command_clear_cache(SimpleNamespace(
            verbose=False, orphan=True, build_cache=False,
        ))
    assert exc.value.code == 1
    assert os.path.exists(cache.layer_cache_path(digest))


def test_list_image_reports_nothing_rather_than_host_files(hijacked, capsys):
    command_list(SimpleNamespace(image=True, quiet=False))
    assert "victim" not in capsys.readouterr().out


def test_a_planted_entry_is_not_a_cache_entry(builders, tmp_path):
    # Nothing but this program writes here, so a symlink under an entry's
    # name was planted; the payload it points at is not an inventory.
    _seed(builders, "real:latest")
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps({
        "image_ref": "planted:latest", "arch": "x86_64",
        "manifest": {"schemaVersion": 2, "config": {"digest": "sha256:0"},
                     "layers": []},
        "repo": "planted", "image_config": {},
    }))
    os.symlink(str(outside), os.path.join(MANIFEST_CACHE_DIR, "beef.json"))

    refs = {record["image_ref"] for record in cache.iter_cached_images()}
    assert refs == {"real:latest"}


def test_blob_size_does_not_follow_a_planted_link(builders, tmp_path):
    # A symlink under a blob's name measures as no blob at all, rather
    # than reporting the size of whatever host file it points at.
    big = tmp_path / "host-file"
    big.write_bytes(b"H" * 4096)
    digest = _seed(builders, "sized:latest")
    blob = cache.layer_cache_path(digest)
    os.unlink(blob)
    os.symlink(str(big), blob)

    record = cache.image_cache_entry("sized:latest", "x86_64")
    assert record["size"] == 0
    assert record["missing"] == 1


def test_a_symlinked_layer_cache_stops_a_removal(hijacked_layers):
    host_dir, _digest, name = hijacked_layers
    with pytest.raises(SystemExit) as exc:
        _remove_image("victim:latest")
    assert exc.value.code == 1
    assert os.path.exists(os.path.join(str(host_dir), name))
    # Nothing was half-done either: the entry the blob belongs to is
    # still there, so the image is still what it was.
    assert cache.image_cache_entry("victim:latest", "x86_64") is not None


@pytest.fixture
def hijacked_layers(tmp_path, builders):
    """The same trick one directory over: `oci_layers` is the symlink."""
    digest = _seed(builders, "victim:latest")
    name = cache.layer_cache_name(digest)
    host_dir = tmp_path / "host-layers"
    shutil.move(LAYER_CACHE_DIR, str(host_dir))
    os.symlink(str(host_dir), LAYER_CACHE_DIR)
    return host_dir, digest, name
