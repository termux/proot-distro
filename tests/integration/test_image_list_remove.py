# Integration tests for the image side of `list` and `remove`:
# `list --image` inventory/rendering and `remove --image` resolution,
# architecture scoping and layer garbage collection.

import hashlib
import json
import os
from types import SimpleNamespace

import pytest

from proot_distro.commands.list import command_list
from proot_distro.commands.remove import command_remove
from proot_distro.helpers.docker.cache import (
    iter_cached_images, layer_cache_path, manifest_cache_path,
    save_manifest_cache,
)
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA, canonical_json
from proot_distro.paths import container_rootfs


def _seed_image(builders, ref, arch, *, layers=1, shared=(), created=None,
                docker_arch="amd64"):
    """Cache an image; return the layer descriptors it alone introduced.

    *shared* descriptors (from another image) are placed first in the
    manifest, exactly as a shared base layer would be.
    """
    own = []
    for i in range(layers):
        digest, size, _diff = builders.seed_cached_layer(
            [{"name": f"etc/{ref}-{arch}-{i}", "type": "file",
              "data": f"{ref}-{arch}-{i}".encode()}]
        )
        own.append({"digest": digest, "size": size,
                    "mediaType": OCI_LAYER_MEDIA})

    config = {"architecture": docker_arch, "os": "linux", "config": {}}
    if created:
        config["created"] = created
    config_bytes = canonical_json(config)
    manifest = {
        "schemaVersion": 2,
        "config": {
            "digest": "sha256:" + hashlib.sha256(config_bytes).hexdigest(),
            "size": len(config_bytes),
        },
        "layers": list(shared) + own,
    }
    save_manifest_cache(ref, arch, manifest, ref.split(":")[0], config)
    return own


def _list_image(**kw):
    command_list(SimpleNamespace(image=True, quiet=False, **kw))


def _remove_image(target, **kw):
    kw.setdefault("verbose", False)
    kw.setdefault("override_arch", None)
    command_remove(SimpleNamespace(target=target, image=True, **kw))


# ----- inventory ----------------------------------------------------------

def test_cached_image_records_ref_arch_and_size(builders):
    layers = _seed_image(builders, "ubuntu:24.04", "x86_64", layers=2)

    records = iter_cached_images()
    assert len(records) == 1
    record = records[0]
    assert record["image_ref"] == "ubuntu:24.04"
    assert record["arch"] == "x86_64"
    assert record["size"] == sum(l["size"] for l in layers)
    assert record["missing"] == 0
    assert record["image_id"]


def test_cached_image_counts_missing_layers(builders):
    layers = _seed_image(builders, "broken:1", "x86_64", layers=2)
    os.remove(layer_cache_path(layers[0]["digest"]))

    record = iter_cached_images()[0]
    assert record["missing"] == 1
    assert record["size"] == layers[1]["size"]


def test_legacy_entry_named_from_installed_container(builders):
    """An entry without image_ref is named via a container's manifest."""
    _seed_image(builders, "legacy/app:2.0", "aarch64", docker_arch="arm64")

    # Strip the metadata the way a pre-`list --image` version wrote it.
    path = manifest_cache_path("legacy/app:2.0", "aarch64")
    with open(path) as fh:
        payload = json.load(fh)
    payload.pop("image_ref")
    payload.pop("arch")
    with open(path, "w") as fh:
        json.dump(payload, fh)

    assert iter_cached_images()[0]["image_ref"] == ""

    builders.make_container("app", manifest={
        "image_ref": "legacy/app:2.0", "arch": "aarch64",
        "manifest": {}, "image_config": {},
    })
    record = iter_cached_images()[0]
    assert record["image_ref"] == "legacy/app:2.0"
    assert record["arch"] == "aarch64"


def test_pull_backfills_legacy_entry(builders, sandbox_tmp):
    """A cache hit repairs an entry that predates the stored metadata."""
    from proot_distro.helpers.docker.pull import pull_image

    _seed_image(builders, "img:1", "x86_64")
    path = manifest_cache_path("img:1", "x86_64")
    with open(path) as fh:
        payload = json.load(fh)
    del payload["image_ref"], payload["arch"]
    with open(path, "w") as fh:
        json.dump(payload, fh)

    rootfs = str(sandbox_tmp / "rootfs")
    os.makedirs(rootfs, exist_ok=True)
    pull_image("img:1", rootfs, "x86_64")

    record = iter_cached_images()[0]
    assert record["image_ref"] == "img:1"
    assert record["arch"] == "x86_64"


# ----- list --image -------------------------------------------------------

def test_list_images_empty(capsys):
    _list_image()
    assert "No images are cached" in capsys.readouterr().err


def test_list_images_table(builders, capsys):
    _seed_image(builders, "ubuntu:24.04", "aarch64", docker_arch="arm64",
                created="2024-04-25T12:00:00Z")
    _seed_image(builders, "myapp:latest", "x86_64")

    _list_image()
    err = capsys.readouterr().err
    assert "IMAGE" in err and "ARCH" in err and "CREATED" in err
    assert "ubuntu:24.04" in err
    assert "aarch64" in err
    assert "myapp:latest" in err
    assert "years ago" in err          # image config timestamp
    assert "just now" in err           # cache mtime fallback
    assert "incomplete" not in err     # every layer is present


def test_list_images_marks_incomplete(builders, capsys):
    layers = _seed_image(builders, "broken:1", "x86_64")
    os.remove(layer_cache_path(layers[0]["digest"]))

    _list_image()
    err = capsys.readouterr().err
    assert "broken:1*" in err
    assert "incomplete" in err


def test_list_images_quiet_prints_unique_refs(builders, capsys):
    _seed_image(builders, "ubuntu:24.04", "aarch64", docker_arch="arm64")
    _seed_image(builders, "ubuntu:24.04", "x86_64")
    _seed_image(builders, "myapp:latest", "x86_64")

    command_list(SimpleNamespace(image=True, quiet=True))
    assert capsys.readouterr().out.split() == ["myapp:latest", "ubuntu:24.04"]


def test_list_containers_unaffected_by_image_flag(builders, capsys):
    builders.make_container("alpha")
    command_list(SimpleNamespace(image=False, quiet=True))
    assert capsys.readouterr().out.split() == ["alpha"]


# ----- remove --image -----------------------------------------------------

def test_remove_image_deletes_manifest_and_layers(builders):
    layers = _seed_image(builders, "myapp:latest", "x86_64", layers=2)

    _remove_image("myapp:latest")

    assert not os.path.exists(manifest_cache_path("myapp:latest", "x86_64"))
    for layer in layers:
        assert not os.path.exists(layer_cache_path(layer["digest"]))
    assert iter_cached_images() == []


def test_remove_image_defaults_tag_to_latest(builders):
    _seed_image(builders, "myapp:latest", "x86_64")
    _remove_image("myapp")
    assert iter_cached_images() == []


def test_remove_image_removes_every_architecture(builders):
    _seed_image(builders, "ubuntu:24.04", "aarch64", docker_arch="arm64")
    _seed_image(builders, "ubuntu:24.04", "x86_64")

    _remove_image("ubuntu:24.04")

    assert iter_cached_images() == []


def test_remove_image_architecture_scopes_deletion(builders):
    _seed_image(builders, "ubuntu:24.04", "aarch64", docker_arch="arm64")
    _seed_image(builders, "ubuntu:24.04", "x86_64")

    _remove_image("ubuntu:24.04", override_arch="aarch64")

    remaining = iter_cached_images()
    assert [r["arch"] for r in remaining] == ["x86_64"]


def test_remove_image_keeps_layers_shared_with_another_image(builders):
    shared = _seed_image(builders, "base:1", "x86_64", layers=1)
    own = _seed_image(builders, "derived:1", "x86_64", layers=1,
                      shared=shared)

    _remove_image("derived:1")

    assert os.path.isfile(layer_cache_path(shared[0]["digest"]))
    assert not os.path.exists(layer_cache_path(own[0]["digest"]))
    assert [r["image_ref"] for r in iter_cached_images()] == ["base:1"]


def test_remove_image_by_id_prefix(builders):
    _seed_image(builders, "myapp:latest", "x86_64")
    image_id = iter_cached_images()[0]["image_id"]

    _remove_image(image_id[:8])

    assert iter_cached_images() == []


def test_remove_image_unknown_errors_with_suggestion(builders, capsys):
    _seed_image(builders, "ubuntu:24.04", "x86_64")

    with pytest.raises(SystemExit) as exc:
        _remove_image("ubuntu:20.04")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "no cached image matches 'ubuntu:20.04'" in err
    assert "ubuntu:24.04" in err
    # Nothing was touched.
    assert len(iter_cached_images()) == 1


def test_remove_image_wrong_arch_suggests_the_cached_one(builders, capsys):
    _seed_image(builders, "myapp:latest", "aarch64", docker_arch="arm64")

    with pytest.raises(SystemExit):
        _remove_image("myapp:latest", override_arch="x86_64")
    err = capsys.readouterr().err
    assert "for architecture 'x86_64'" in err
    assert "aarch64" in err


def test_remove_image_rejects_unknown_architecture(builders, capsys):
    _seed_image(builders, "myapp:latest", "x86_64")

    with pytest.raises(SystemExit) as exc:
        _remove_image("myapp:latest", override_arch="sparc")
    assert exc.value.code == 1
    assert "unknown architecture" in capsys.readouterr().err
    assert len(iter_cached_images()) == 1


def test_remove_image_reports_dependent_containers(builders, capsys):
    _seed_image(builders, "ubuntu:24.04", "x86_64")
    builders.make_container("ubuntu", manifest={
        "image_ref": "ubuntu:24.04", "arch": "x86_64",
        "manifest": {}, "image_config": {},
    })

    _remove_image("ubuntu:24.04")

    err = capsys.readouterr().err
    assert "'ubuntu'" in err and "keep working" in err
    # The container itself survives.
    assert os.path.isdir(container_rootfs("ubuntu"))


def test_remove_image_refuses_while_build_lock_held(builders, capsys):
    from proot_distro.locking import BuildLock

    _seed_image(builders, "myapp:latest", "x86_64")

    lock = BuildLock("myapp:latest", "x86_64", command="build")
    assert lock.acquire()
    # Re-entrancy would defeat the test: this process now "owns" the lock.
    from proot_distro import locking
    locking._held_exclusive.discard(lock.lock_path)
    try:
        with pytest.raises(SystemExit) as exc:
            _remove_image("myapp:latest")
    finally:
        lock.release()
    assert exc.value.code == 1
    assert "busy" in capsys.readouterr().err
    assert len(iter_cached_images()) == 1


def test_remove_container_rejects_architecture_flag(builders, capsys):
    builders.make_container("box")
    with pytest.raises(SystemExit) as exc:
        command_remove(SimpleNamespace(target="box", image=False,
                                       verbose=False, override_arch="x86_64"))
    assert exc.value.code == 1
    assert "--architecture" in capsys.readouterr().err
