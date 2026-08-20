# Containment tests for `clear-cache`, which walks the download cache
# before deleting it.
#
# That directory is guest-writable — on Termux it sits under the
# $TERMUX_PREFIX bound read-write into every non-isolated container — and
# the measuring pass used os.walk() with os.stat()/os.chmod() on each
# name, all of which follow a symlink. A planted entry therefore had its
# host target chmod'ed u+rw on the way past, and a symlinked oci_layers
# handed the orphan sweep a directory of host files to unlink.

import os
import shutil
import stat
from types import SimpleNamespace

import pytest

from proot_distro import statedir
from proot_distro.commands.clear_cache import command_clear_cache
from proot_distro.constants import BASE_CACHE_DIR, LAYER_CACHE_DIR


def _args(**kw):
    base = {"verbose": False, "orphan": False, "build_cache": False}
    base.update(kw)
    return SimpleNamespace(**base)


@pytest.fixture
def victim(tmp_path):
    path = tmp_path / "host-file"
    path.write_text("host content\n")
    path.chmod(0o400)
    return path


def test_measuring_pass_does_not_chmod_through_a_symlink(victim):
    os.symlink(str(victim), os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef"))

    command_clear_cache(_args())

    assert stat.S_IMODE(victim.stat().st_mode) == 0o400
    assert victim.read_text() == "host content\n"
    assert not os.path.exists(LAYER_CACHE_DIR)


def test_measuring_pass_does_not_chmod_a_symlinked_directory(tmp_path):
    sealed = tmp_path / "sealed"
    sealed.mkdir(mode=0o500)
    os.symlink(str(sealed), os.path.join(LAYER_CACHE_DIR, "subdir"))
    try:
        command_clear_cache(_args())
        assert stat.S_IMODE(sealed.stat().st_mode) == 0o500
        assert sealed.exists()
    finally:
        sealed.chmod(0o700)


def test_planted_symlink_is_unlinked_not_followed(victim):
    os.symlink(str(victim), os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef"))
    command_clear_cache(_args())
    # The link went with the cache; what it pointed at did not.
    assert victim.exists()


def test_size_excludes_what_a_symlink_points_at(victim, capsys):
    victim.chmod(0o600)
    victim.write_bytes(b"x" * 4096)
    os.symlink(str(victim), os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef"))
    with open(os.path.join(LAYER_CACHE_DIR, "sha256_real"), "wb") as fh:
        fh.write(b"y" * 10)

    command_clear_cache(_args())
    err = capsys.readouterr().err
    assert "Reclaimed 10 B" in err


def test_sealed_directory_is_still_measured_and_removed():
    sub = os.path.join(LAYER_CACHE_DIR, "sealed")
    os.mkdir(sub)
    with open(os.path.join(sub, "blob"), "wb") as fh:
        fh.write(b"z" * 100)
    os.chmod(sub, 0o000)

    command_clear_cache(_args())
    assert not os.path.exists(LAYER_CACHE_DIR)


def test_orphan_sweep_refuses_a_symlinked_layer_cache(tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sha256_something").write_text("host blob\n")
    os.rmdir(LAYER_CACHE_DIR)
    os.symlink(str(outside), LAYER_CACHE_DIR)

    with pytest.raises(SystemExit) as exc:
        command_clear_cache(_args(orphan=True))
    assert exc.value.code == 1
    assert "layer cache" in capsys.readouterr().err
    assert (outside / "sha256_something").exists()


def test_orphan_sweep_reports_a_missing_layer_cache_as_empty(capsys):
    os.rmdir(LAYER_CACHE_DIR)
    command_clear_cache(_args(orphan=True))
    assert "No orphan layers found." in capsys.readouterr().err


def test_deep_tree_does_not_recurse():
    # A tree deeper than the interpreter's limit, which the guest is free
    # to create: neither the measuring pass nor the removal may recurse.
    deep = os.path.join(BASE_CACHE_DIR, "deep")
    os.mkdir(deep)
    fd = os.open(deep, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for _ in range(1200):
            os.mkdir("d", dir_fd=fd)
            nxt = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = nxt
        with open(os.path.join("blob"), "wb", opener=lambda p, f: os.open(
                p, f, 0o644, dir_fd=fd)) as fh:
            fh.write(b"q" * 8)
    finally:
        os.close(fd)

    command_clear_cache(_args())
    assert not os.path.exists(deep)


def test_sealed_cache_root_is_still_emptied():
    # os.walk() used to relax the root itself on its way past; the walk
    # that replaced it never meets that directory, only what is under it.
    sub = os.path.join(LAYER_CACHE_DIR, "sub")
    os.mkdir(sub)
    with open(os.path.join(sub, "blob"), "wb") as fh:
        fh.write(b"z" * 64)
    os.chmod(BASE_CACHE_DIR, 0o000)
    try:
        command_clear_cache(_args())
    finally:
        os.chmod(BASE_CACHE_DIR, 0o700)
    assert os.listdir(BASE_CACHE_DIR) == []


# --- the cache root itself -------------------------------------------------
#
# On Termux BASE_CACHE_DIR sits inside RUNTIME_DIR, so `cache` is one more
# guest-writable name; off Termux it is a trust root of its own and is
# named as one. The suite runs non-Termux, so the enclosing root is moved
# one level up to give the walk the same shape the Termux layout has.

@pytest.fixture
def cache_inside_a_root(monkeypatch):
    monkeypatch.setattr(
        statedir, "STATE_ROOTS", (os.path.dirname(BASE_CACHE_DIR),),
    )
    yield
    # The per-test wipe cannot clear a symlink standing where the cache
    # root belongs (shutil.rmtree refuses one), so put the real directory
    # back before the next test looks at it.
    if os.path.islink(BASE_CACHE_DIR):
        os.unlink(BASE_CACHE_DIR)
    os.makedirs(LAYER_CACHE_DIR, exist_ok=True)


def test_symlinked_cache_root_is_refused(cache_inside_a_root, tmp_path,
                                         capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keepsake").write_text("host content\n")
    shutil.rmtree(BASE_CACHE_DIR)
    os.symlink(str(outside), BASE_CACHE_DIR)

    with pytest.raises(SystemExit) as exc:
        command_clear_cache(_args())
    assert exc.value.code == 1
    assert "cannot read the cache directory" in capsys.readouterr().err
    assert (outside / "keepsake").exists()


@pytest.mark.parametrize("flag", ["orphan", "build_cache"])
def test_symlinked_cache_root_is_refused_by_the_sweep(
        flag, cache_inside_a_root, tmp_path, capsys):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sha256_something").write_text("host blob\n")
    shutil.rmtree(BASE_CACHE_DIR)
    os.symlink(str(outside), BASE_CACHE_DIR)

    with pytest.raises(SystemExit) as exc:
        command_clear_cache(_args(**{flag: True}))
    assert exc.value.code == 1
    # Whichever guard is reached first — the build index cannot be read
    # under --orphan, and cannot be removed under --build-cache — the
    # sweep stops before deleting anything.
    assert "Nothing was removed" in capsys.readouterr().err
    assert (outside / "sha256_something").exists()


def test_a_real_cache_root_still_works(cache_inside_a_root):
    with open(os.path.join(LAYER_CACHE_DIR, "sha256_real"), "wb") as fh:
        fh.write(b"y" * 10)
    command_clear_cache(_args())
    assert os.listdir(BASE_CACHE_DIR) == []


def test_missing_cache_root_reports_empty(cache_inside_a_root, capsys):
    shutil.rmtree(BASE_CACHE_DIR)
    command_clear_cache(_args())
    assert "Cache is empty." in capsys.readouterr().err
