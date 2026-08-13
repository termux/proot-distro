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
