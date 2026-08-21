# Integration tests for `command_clear_cache` and `command_list`.

import os
from types import SimpleNamespace

import pytest

from proot_distro.commands.clear_cache import command_clear_cache
from proot_distro.commands.list import command_list
from proot_distro.constants import LAYER_CACHE_DIR, MANIFEST_CACHE_DIR
from proot_distro.helpers import build_cache
from proot_distro.helpers.docker.cache import (
    layer_cache_path, save_manifest_cache,
)
from proot_distro.locking import ContainerLock


def test_clear_cache_removes_everything(builders):
    builders.seed_cached_layer([{"name": "x", "type": "file", "data": b"1"}])
    save_manifest_cache("img:1", "x86_64", {"layers": []}, "library/img", {})
    build_cache.record("hash1", "sha256:l", "sha256:d", 10)

    assert os.listdir(LAYER_CACHE_DIR)
    assert os.listdir(MANIFEST_CACHE_DIR)
    assert os.path.exists(build_cache._INDEX_PATH)

    command_clear_cache(SimpleNamespace(verbose=False))

    # Top-level cache entries are gone.
    assert not os.path.exists(LAYER_CACHE_DIR)
    assert not os.path.exists(MANIFEST_CACHE_DIR)
    assert not os.path.exists(build_cache._INDEX_PATH)


def test_clear_cache_empty_is_safe(capsys):
    import shutil
    from proot_distro.constants import BASE_CACHE_DIR
    # Remove the dir entirely so the "cache is empty" branch is exercised.
    shutil.rmtree(BASE_CACHE_DIR, ignore_errors=True)
    command_clear_cache(SimpleNamespace(verbose=False))
    assert "empty" in capsys.readouterr().err.lower()


# ---------------------------------------------------------------------------
# clear-cache --orphan
# ---------------------------------------------------------------------------

def _orphan(verbose=False):
    return SimpleNamespace(orphan=True, verbose=verbose)


def test_orphan_removes_only_unreferenced_layers(builders, capsys):
    kept, _, _ = builders.seed_cached_layer(
        [{"name": "kept", "type": "file", "data": b"1"}]
    )
    orphan, _, _ = builders.seed_cached_layer(
        [{"name": "orphan", "type": "file", "data": b"2" * 4096}]
    )
    save_manifest_cache(
        "img:1", "x86_64", {"layers": [{"digest": kept}]}, "library/img", {},
    )
    build_cache.record("hash1", "sha256:someotherlayer", "sha256:d", 10)

    command_clear_cache(_orphan())

    assert os.path.isfile(layer_cache_path(kept))
    assert not os.path.exists(layer_cache_path(orphan))
    # Everything that is not a layer blob is left alone.
    assert os.listdir(MANIFEST_CACHE_DIR)
    assert os.path.exists(build_cache._INDEX_PATH)
    err = capsys.readouterr().err
    assert "1 orphan layer" in err
    assert "Reclaimed" in err


def test_orphan_keeps_layers_pinned_by_the_build_cache(builders):
    """A build-cache entry is a reference even with no manifest naming it."""
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    build_cache.record("hash1", digest, diff_id, size)

    command_clear_cache(_orphan())

    assert os.path.isfile(layer_cache_path(digest))


def test_orphan_keeps_the_config_blob_a_manifest_names(builders):
    digest, _, _ = builders.seed_cached_layer(
        [{"name": "cfg", "type": "file", "data": b"1"}]
    )
    save_manifest_cache(
        "img:1", "x86_64",
        {"layers": [], "config": {"digest": digest}}, "library/img", {},
    )

    command_clear_cache(_orphan())

    assert os.path.isfile(layer_cache_path(digest))


def test_orphan_collects_interrupted_download_leftovers(builders):
    stale = os.path.join(LAYER_CACHE_DIR, "sha256_abc.12345.tmp")
    with open(stale, "wb") as fh:
        fh.write(b"partial")

    command_clear_cache(_orphan())

    assert not os.path.exists(stale)


def test_orphan_aborts_on_an_unreadable_manifest_entry(builders, capsys):
    digest, _, _ = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    with open(os.path.join(MANIFEST_CACHE_DIR, "broken.json"), "w") as fh:
        fh.write("{ this is not json")

    with pytest.raises(SystemExit) as exc:
        command_clear_cache(_orphan())

    assert exc.value.code == 1
    # An entry that cannot be read is not an entry without references.
    assert os.path.isfile(layer_cache_path(digest))
    assert "broken.json" in capsys.readouterr().err


@pytest.mark.parametrize("payload", [
    {"manifest": {"layers": 1}},
    {"manifest": {"layers": [{"digest": 123}]}},
    {"manifest": {"layers": [], "config": "bad"}},
    {"manifest": {"layers": [], "config": {"digest": 123}}},
])
def test_orphan_aborts_on_a_malformed_manifest_entry(payload, builders,
                                                     capsys):
    # A manifest whose shape is not a manifest's used to end the sweep in
    # a TypeError or an AttributeError before it decided anything. It is
    # an entry that cannot be read, and an entry that cannot be read is
    # not an entry with no references.
    import json
    digest, _, _ = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    with open(os.path.join(MANIFEST_CACHE_DIR, "broken.json"), "w") as fh:
        json.dump(payload, fh)

    with pytest.raises(SystemExit) as exc:
        command_clear_cache(_orphan())

    assert exc.value.code == 1
    assert os.path.isfile(layer_cache_path(digest))
    assert "broken.json" in capsys.readouterr().err


def test_orphan_aborts_on_an_unreadable_build_index(builders, capsys):
    digest, _, _ = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    with open(build_cache._INDEX_PATH, "w") as fh:
        fh.write("{ not json either")

    with pytest.raises(SystemExit) as exc:
        command_clear_cache(_orphan())

    assert exc.value.code == 1
    assert os.path.isfile(layer_cache_path(digest))
    assert "build cache index" in capsys.readouterr().err


def test_orphan_refuses_while_another_command_holds_a_lock(builders, capsys):
    digest, _, _ = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )

    with ContainerLock("busybox", exclusive=True, command="install"):
        with pytest.raises(SystemExit) as exc:
            command_clear_cache(_orphan())

    assert exc.value.code == 1
    assert os.path.isfile(layer_cache_path(digest))
    assert "is running" in capsys.readouterr().err


def test_orphan_ignores_shared_locks(builders):
    """`login` and `backup` hold shared locks and never write to the cache."""
    digest, _, _ = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )

    with ContainerLock("busybox", exclusive=False, command="login"):
        command_clear_cache(_orphan())

    assert not os.path.exists(layer_cache_path(digest))


def test_orphan_on_an_empty_cache_says_so(capsys):
    command_clear_cache(_orphan())
    assert "No orphan layers" in capsys.readouterr().err


def test_orphan_verbose_names_each_removed_blob(builders, capsys):
    digest, _, _ = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )

    command_clear_cache(_orphan(verbose=True))

    assert digest.replace(":", "_") in capsys.readouterr().err


# ---------------------------------------------------------------------------
# clear-cache --build-cache
# ---------------------------------------------------------------------------

def _build_cache(verbose=False, orphan=False):
    return SimpleNamespace(orphan=orphan, build_cache=True, verbose=verbose)


def test_build_cache_drops_the_index_and_the_layers_it_pinned(
    builders, capsys,
):
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    build_cache.record("hash1", digest, diff_id, size)

    command_clear_cache(_build_cache())

    assert not os.path.exists(build_cache._INDEX_PATH)
    assert not os.path.exists(layer_cache_path(digest))
    err = capsys.readouterr().err
    assert "build cache index" in err
    assert "Reclaimed" in err


def test_build_cache_keeps_layers_a_cached_image_lists(builders):
    """A build's output image still names its layers; only the index goes."""
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    build_cache.record("hash1", digest, diff_id, size)
    save_manifest_cache(
        "built:1", "x86_64", {"layers": [{"digest": digest}]},
        "library/built", {},
    )

    command_clear_cache(_build_cache())

    assert not os.path.exists(build_cache._INDEX_PATH)
    assert os.path.isfile(layer_cache_path(digest))


def test_build_cache_leaves_the_manifest_cache_alone(builders):
    digest, _, _ = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    save_manifest_cache(
        "img:1", "x86_64", {"layers": [{"digest": digest}]}, "library/img", {},
    )

    command_clear_cache(_build_cache())

    assert os.listdir(MANIFEST_CACHE_DIR)
    assert os.path.isfile(layer_cache_path(digest))


def test_build_cache_works_on_an_index_too_corrupt_to_parse(builders, capsys):
    """The flag unlinks the index, never reads it - which is the point."""
    digest, _, _ = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    with open(build_cache._INDEX_PATH, "w") as fh:
        fh.write("{ not json either")

    command_clear_cache(_build_cache())

    assert not os.path.exists(build_cache._INDEX_PATH)
    assert not os.path.exists(layer_cache_path(digest))


def test_build_cache_still_aborts_on_an_unreadable_manifest_entry(
    builders, capsys,
):
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    build_cache.record("hash1", digest, diff_id, size)
    with open(os.path.join(MANIFEST_CACHE_DIR, "broken.json"), "w") as fh:
        fh.write("{ this is not json")

    with pytest.raises(SystemExit) as exc:
        command_clear_cache(_build_cache())

    assert exc.value.code == 1
    # The keep set is computed before anything is deleted.
    assert os.path.exists(build_cache._INDEX_PATH)
    assert os.path.isfile(layer_cache_path(digest))
    assert "broken.json" in capsys.readouterr().err


def test_build_cache_refuses_while_another_command_holds_a_lock(
    builders, capsys,
):
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    build_cache.record("hash1", digest, diff_id, size)

    with ContainerLock("busybox", exclusive=True, command="build"):
        with pytest.raises(SystemExit) as exc:
            command_clear_cache(_build_cache())

    assert exc.value.code == 1
    assert os.path.exists(build_cache._INDEX_PATH)
    assert os.path.isfile(layer_cache_path(digest))
    assert "is running" in capsys.readouterr().err


def test_build_cache_on_an_empty_cache_says_so(capsys):
    command_clear_cache(_build_cache())
    assert "build cache is already empty" in capsys.readouterr().err.lower()


def test_build_cache_reports_the_index_alone_when_nothing_is_collectable(
    builders, capsys,
):
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    build_cache.record("hash1", digest, diff_id, size)
    save_manifest_cache(
        "built:1", "x86_64", {"layers": [{"digest": digest}]},
        "library/built", {},
    )

    command_clear_cache(_build_cache())

    err = capsys.readouterr().err
    assert "orphan layer" not in err
    assert "Reclaimed" in err


def test_build_cache_alongside_orphan_is_the_same_sweep(builders):
    """--orphan adds a root that --build-cache removes; passing both is fine."""
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    build_cache.record("hash1", digest, diff_id, size)

    command_clear_cache(_build_cache(orphan=True))

    assert not os.path.exists(build_cache._INDEX_PATH)
    assert not os.path.exists(layer_cache_path(digest))


def test_build_cache_verbose_names_the_index_and_each_blob(builders, capsys):
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "x", "type": "file", "data": b"1"}]
    )
    build_cache.record("hash1", digest, diff_id, size)

    command_clear_cache(_build_cache(verbose=True))

    err = capsys.readouterr().err
    assert "build_cache_index.json" in err
    assert digest.replace(":", "_") in err


def test_orphan_names_build_cache_when_the_index_cannot_be_read(
    builders, capsys,
):
    """The remedy for a corrupt index is the new flag, so the error says so."""
    builders.seed_cached_layer([{"name": "x", "type": "file", "data": b"1"}])
    with open(build_cache._INDEX_PATH, "w") as fh:
        fh.write("{ not json")

    with pytest.raises(SystemExit):
        command_clear_cache(_orphan())

    assert "--build-cache" in capsys.readouterr().err


def test_list_empty(capsys):
    command_list(SimpleNamespace(quiet=False))
    assert "No containers" in capsys.readouterr().err


def test_list_shows_containers(builders, capsys):
    builders.make_container("alpha")
    builders.make_container("beta")
    command_list(SimpleNamespace(quiet=False))
    err = capsys.readouterr().err
    assert "alpha" in err
    assert "beta" in err


def test_list_quiet_prints_names_to_stdout(builders, capsys):
    builders.make_container("alpha")
    builders.make_container("beta")
    command_list(SimpleNamespace(quiet=True))
    out = capsys.readouterr().out
    assert out.split() == ["alpha", "beta"]
