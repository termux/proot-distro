# Containment tests for the container directory itself.
#
# containers/<name> is composed lexically from a validated name, but the
# directory it names is guest-writable on Termux, where the runtime tree
# sits under the $TERMUX_PREFIX bound read-write into every non-isolated
# container. A session can therefore leave `containers/<name>` behind as
# a symlink to any directory the user can write, and every command that
# creates, fills or discards a container used to reach it by name:
# os.path.isdir() said "not installed", os.makedirs(exist_ok=True)
# accepted the link, and the whole install was extracted inside whatever
# it led to.

import os
from types import SimpleNamespace

import pytest

from proot_distro.commands.install import command_install
from proot_distro.commands.remove import command_remove
from proot_distro.commands.reset import command_reset
from proot_distro.constants import BASE_CACHE_DIR, CONTAINERS_DIR
from proot_distro.paths import container_dir, container_rootfs


def _install_args(image_ref, name=None):
    return SimpleNamespace(
        image_ref=image_ref, custom_container_name=name, override_arch=None,
    )


@pytest.fixture
def archive(tmp_path, builders):
    arc = tmp_path / "box.tar.gz"
    builders.make_tar(str(arc), builders.rootfs_members(), compression="gz")
    return str(arc)


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    (d / "keepsake").write_text("host content\n")
    return d


def _plant(name, target):
    os.symlink(str(target), container_dir(name))


# --- install ---------------------------------------------------------------

def test_install_refuses_a_symlinked_container_dir(archive, outside, capsys):
    _plant("box", outside)

    with pytest.raises(SystemExit) as exc:
        command_install(_install_args(archive, name="box"))
    assert exc.value.code == 1
    assert "is not usable" in capsys.readouterr().err
    # Nothing was extracted into the host directory the link named.
    assert sorted(os.listdir(str(outside))) == ["keepsake"]


def test_install_refuses_a_symlinked_container_dir_holding_a_rootfs(
        archive, outside, capsys):
    # The other half of the same plant: with a rootfs already there, the
    # old check reported the container as installed instead.
    (outside / "rootfs").mkdir()
    _plant("box", outside)

    with pytest.raises(SystemExit) as exc:
        command_install(_install_args(archive, name="box"))
    assert exc.value.code == 1
    assert "is not usable" in capsys.readouterr().err
    assert os.listdir(str(outside / "rootfs")) == []


def test_install_refuses_a_symlinked_rootfs(archive, outside, capsys):
    os.makedirs(container_dir("box"))
    os.symlink(str(outside), container_rootfs("box"))

    with pytest.raises(SystemExit) as exc:
        command_install(_install_args(archive, name="box"))
    assert exc.value.code == 1
    assert "is not usable" in capsys.readouterr().err
    assert sorted(os.listdir(str(outside))) == ["keepsake"]


def test_install_refuses_a_file_where_the_container_dir_belongs(
        archive, capsys):
    with open(container_dir("box"), "w") as fh:
        fh.write("not a directory")

    with pytest.raises(SystemExit) as exc:
        command_install(_install_args(archive, name="box"))
    assert exc.value.code == 1
    assert "is not usable" in capsys.readouterr().err


def test_install_still_creates_a_fresh_container(archive):
    command_install(_install_args(archive, name="box"))
    assert os.path.isdir(container_rootfs("box"))
    assert os.path.isdir(os.path.join(container_dir("box"), "sysdata"))


def test_failed_install_does_not_clean_through_a_planted_parent(
        tmp_path, outside, capsys):
    # The cleanup path is a removal, which is worse than a write: a
    # planted `containers` would have aimed it at a host directory.
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a tar archive at all")

    with pytest.raises(SystemExit):
        command_install(_install_args(str(bad), name="box"))
    assert not os.path.exists(container_dir("box"))


# --- reset -----------------------------------------------------------------

def test_reset_refuses_a_symlinked_container_dir(outside, capsys):
    (outside / "rootfs").mkdir()
    (outside / "rootfs" / "file").write_text("host content\n")
    _plant("box", outside)

    with pytest.raises(SystemExit) as exc:
        command_reset(SimpleNamespace(container_name="box"))
    assert exc.value.code == 1
    assert "is not usable" in capsys.readouterr().err
    assert (outside / "rootfs" / "file").exists()


# --- remove ----------------------------------------------------------------

def test_remove_unlinks_a_planted_entry_without_following_it(outside):
    # Removing junk a container left behind is the one thing that should
    # still work on a planted name -- the link goes, its target does not.
    (outside / "rootfs").mkdir()
    _plant("box", outside)

    command_remove(SimpleNamespace(
        target="box", verbose=False, image=False, override_arch=None,
    ))
    assert not os.path.lexists(container_dir("box"))
    assert (outside / "keepsake").exists()
    assert (outside / "rootfs").exists()


# --- the download cache ----------------------------------------------------

def test_url_temporary_lands_in_the_cache():
    from proot_distro.commands.install import _cache_temp_file

    path = _cache_temp_file("dl_install_box")
    assert os.path.dirname(path) == BASE_CACHE_DIR
    assert os.path.isfile(path)
    assert len(os.fsencode(os.path.basename(path))) <= 255


def test_url_temporary_is_refused_through_a_symlinked_cache(
        monkeypatch, tmp_path):
    from proot_distro import statedir
    from proot_distro.commands.install import _cache_temp_file

    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(
        statedir, "STATE_ROOTS", (os.path.dirname(BASE_CACHE_DIR),))
    import shutil
    shutil.rmtree(BASE_CACHE_DIR)
    os.symlink(str(outside), BASE_CACHE_DIR)
    try:
        with pytest.raises(OSError):
            _cache_temp_file("dl_install_box")
        assert os.listdir(str(outside)) == []
    finally:
        os.unlink(BASE_CACHE_DIR)
        os.makedirs(BASE_CACHE_DIR, exist_ok=True)


def test_containers_dir_is_below_the_trust_root():
    from proot_distro import statedir

    root, parts = statedir.split_state_path(container_dir("box"))
    assert root is not None
    assert parts[-2:] == ("containers", "box")
    assert os.path.join(root, *parts) == os.path.join(CONTAINERS_DIR, "box")
