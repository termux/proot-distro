# Containment tests for the two commands that *move* a container tree.
#
# rename moves containers/<old> to containers/<new>, and the legacy
# migration moves installed-rootfs/<name> into containers/<name>/rootfs.
# Both used os.path.isdir() on the composed name and then os.rename() on
# it, and both follow a symlink: a guest that left either name behind as
# a link to a host directory had the link moved into place as a
# container, after which the l2s rewrite -- which unlinks entries and
# creates symlinks -- walked that host directory, and so did every
# session afterwards.

import os

import pytest

from proot_distro.commands.login.migrate import migrate_legacy_rootfs
from proot_distro.commands.rename import command_rename
from proot_distro.constants import LEGACY_ROOTFS_DIR
from proot_distro.paths import container_dir, container_rootfs


def _args(orig, new):
    return type("A", (), {"orig_name": orig, "new_name": new})()


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    (d / "keepsake").write_text("host content\n")
    return d


# --- rename ----------------------------------------------------------------

def test_rename_moves_the_tree_and_rewrites_l2s(builders):
    builders.make_container("old")
    old_rootfs = container_rootfs("old")
    os.mkdir(os.path.join(old_rootfs, ".l2s"))
    os.symlink(old_rootfs + "/.l2s/file0001",
               os.path.join(old_rootfs, "etc", "linked"))
    os.symlink("/somewhere/else", os.path.join(old_rootfs, "etc", "other"))

    command_rename(_args("old", "new"))

    assert not os.path.lexists(container_dir("old"))
    new_rootfs = container_rootfs("new")
    assert os.readlink(os.path.join(new_rootfs, "etc", "linked")) == \
        new_rootfs + "/.l2s/file0001"
    assert os.readlink(os.path.join(new_rootfs, "etc", "other")) == \
        "/somewhere/else"


def test_rename_refuses_a_planted_source(outside, capsys):
    (outside / "rootfs").mkdir()
    os.symlink(str(outside), container_dir("old"))

    with pytest.raises(SystemExit) as exc:
        command_rename(_args("old", "new"))
    assert exc.value.code == 1
    assert "is not usable" in capsys.readouterr().err
    assert os.path.islink(container_dir("old"))
    assert not os.path.lexists(container_dir("new"))
    assert (outside / "keepsake").exists()


def test_rename_refuses_a_planted_destination(builders, outside, capsys):
    builders.make_container("old")
    os.symlink(str(outside), container_dir("new"))

    with pytest.raises(SystemExit) as exc:
        command_rename(_args("old", "new"))
    assert exc.value.code == 1
    assert "is not usable" in capsys.readouterr().err
    assert os.path.isdir(container_rootfs("old"))
    assert sorted(os.listdir(str(outside))) == ["keepsake"]


def test_rename_refuses_an_existing_destination(builders, capsys):
    builders.make_container("old")
    builders.make_container("new")

    with pytest.raises(SystemExit) as exc:
        command_rename(_args("old", "new"))
    assert exc.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_rename_refuses_a_missing_source(capsys):
    with pytest.raises(SystemExit) as exc:
        command_rename(_args("nope", "new"))
    assert exc.value.code == 1
    assert "is not installed" in capsys.readouterr().err


# --- legacy migration ------------------------------------------------------

def _make_legacy(name):
    legacy = os.path.join(LEGACY_ROOTFS_DIR, name)
    os.makedirs(os.path.join(legacy, "etc"))
    with open(os.path.join(legacy, "etc", "hostname"), "w") as fh:
        fh.write("guest\n")
    return legacy


def test_migration_moves_the_legacy_tree_and_rewrites_l2s():
    legacy = _make_legacy("box")
    os.symlink(legacy + "/.l2s/file0001", os.path.join(legacy, "etc", "l"))

    migrate_legacy_rootfs("box")

    rootfs = container_rootfs("box")
    assert not os.path.lexists(legacy)
    assert open(os.path.join(rootfs, "etc", "hostname")).read() == "guest\n"
    assert os.readlink(os.path.join(rootfs, "etc", "l")) == \
        rootfs + "/.l2s/file0001"


def test_migration_is_skipped_once_the_container_exists(builders):
    legacy = _make_legacy("box")
    builders.make_container("box")

    migrate_legacy_rootfs("box")

    # Nothing moved: the installed container wins and the legacy tree is
    # left where it is.
    assert os.path.isdir(legacy)
    assert not os.path.exists(
        os.path.join(container_rootfs("box"), "etc", "hostname"))


def test_migration_will_not_move_a_planted_legacy_entry(outside):
    os.makedirs(LEGACY_ROOTFS_DIR, exist_ok=True)
    os.symlink(str(outside), os.path.join(LEGACY_ROOTFS_DIR, "box"))

    migrate_legacy_rootfs("box")

    # The link is not a legacy rootfs, so nothing was moved and nothing
    # inside the host directory was touched.
    assert os.path.islink(os.path.join(LEGACY_ROOTFS_DIR, "box"))
    assert not os.path.lexists(container_rootfs("box"))
    assert sorted(os.listdir(str(outside))) == ["keepsake"]


def test_migration_refuses_a_planted_container_dir(outside, capsys):
    _make_legacy("box")
    os.symlink(str(outside), container_dir("box"))

    with pytest.raises(SystemExit) as exc:
        migrate_legacy_rootfs("box")
    assert exc.value.code == 1
    assert "is not usable" in capsys.readouterr().err
    assert sorted(os.listdir(str(outside))) == ["keepsake"]
