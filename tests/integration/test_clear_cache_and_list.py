# Integration tests for `command_clear_cache` and `command_list`.

import os
from types import SimpleNamespace

from proot_distro.commands.clear_cache import command_clear_cache
from proot_distro.commands.list import command_list
from proot_distro.constants import LAYER_CACHE_DIR, MANIFEST_CACHE_DIR
from proot_distro.helpers import build_cache
from proot_distro.helpers.docker.cache import save_manifest_cache


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
