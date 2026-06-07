# Integration tests for `command_remove`, `command_rename`, and
# `command_reset`.

import os
from types import SimpleNamespace

import pytest

from proot_distro.commands.remove import command_remove
from proot_distro.commands.rename import command_rename
from proot_distro.commands.reset import command_reset
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA
from proot_distro.paths import container_dir, container_rootfs


# ----- remove -------------------------------------------------------------

def test_remove_deletes_container(builders):
    builders.make_container("box")
    command_remove(SimpleNamespace(container_name="box", verbose=False))
    assert not os.path.exists(container_dir("box"))


def test_remove_missing_errors(capsys):
    with pytest.raises(SystemExit) as exc:
        command_remove(SimpleNamespace(container_name="ghost", verbose=False))
    assert exc.value.code == 1
    assert "is not installed" in capsys.readouterr().err


def test_remove_handles_chmod000_subtree(builders):
    builders.make_container("box")
    locked = os.path.join(container_rootfs("box"), "locked")
    os.makedirs(locked)
    os.chmod(locked, 0o000)
    command_remove(SimpleNamespace(container_name="box", verbose=False))
    assert not os.path.exists(container_dir("box"))


# ----- rename -------------------------------------------------------------

def test_rename_moves_directory(builders):
    builders.make_container("old")
    command_rename(SimpleNamespace(orig_name="old", new_name="new"))
    assert not os.path.exists(container_dir("old"))
    assert os.path.isdir(container_rootfs("new"))


def test_rename_rewrites_l2s_symlinks(builders):
    builders.make_container("old")
    old_rootfs = container_rootfs("old")
    os.makedirs(os.path.join(old_rootfs, "app"), exist_ok=True)
    # A symlink whose target points into the OLD absolute rootfs path.
    os.symlink(old_rootfs + "/.l2s/file0001",
               os.path.join(old_rootfs, "app", "link"))
    command_rename(SimpleNamespace(orig_name="old", new_name="new"))
    new_link = os.path.join(container_rootfs("new"), "app", "link")
    assert os.readlink(new_link) == container_rootfs("new") + "/.l2s/file0001"


def test_rename_existing_target_errors(builders, capsys):
    builders.make_container("a")
    builders.make_container("b")
    with pytest.raises(SystemExit) as exc:
        command_rename(SimpleNamespace(orig_name="a", new_name="b"))
    assert exc.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_rename_same_name_errors(builders, capsys):
    builders.make_container("a")
    with pytest.raises(SystemExit) as exc:
        command_rename(SimpleNamespace(orig_name="a", new_name="a"))
    assert exc.value.code == 1
    assert "must differ" in capsys.readouterr().err


def test_rename_missing_source_errors(capsys):
    with pytest.raises(SystemExit) as exc:
        command_rename(SimpleNamespace(orig_name="ghost", new_name="new"))
    assert exc.value.code == 1
    assert "is not installed" in capsys.readouterr().err


# ----- reset --------------------------------------------------------------

def test_reset_without_image_ref_errors(builders, capsys):
    # No manifest -> no OCI image_ref -> reset unsupported.
    builders.make_container("plain")
    with pytest.raises(SystemExit) as exc:
        command_reset(SimpleNamespace(container_name="plain"))
    assert exc.value.code == 1
    assert "OCI" in capsys.readouterr().err


def test_reset_rebuilds_from_cached_image(builders):
    arch = "x86_64"
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "etc/hostname", "type": "file", "data": b"reset-built\n"}]
    )
    manifest = {"schemaVersion": 2,
                "layers": [{"digest": digest, "size": size,
                            "mediaType": OCI_LAYER_MEDIA}]}
    save_manifest_cache("img:1", arch, manifest, "library/img",
                        {"config": {}, "rootfs": {"diff_ids": [diff_id]}})

    builders.make_container("rb", manifest={
        "image_ref": "img:1", "arch": arch,
        "manifest": manifest, "image_config": {"config": {}},
    })
    # Corrupt the current rootfs so we can prove it gets rebuilt.
    with open(os.path.join(container_rootfs("rb"), "etc", "hostname"), "wb") as fh:
        fh.write(b"STALE")

    command_reset(SimpleNamespace(container_name="rb"))

    rebuilt = os.path.join(container_rootfs("rb"), "etc", "hostname")
    assert open(rebuilt, "rb").read() == b"reset-built\n"


def test_reset_missing_container_errors(capsys):
    with pytest.raises(SystemExit) as exc:
        command_reset(SimpleNamespace(container_name="ghost"))
    assert exc.value.code == 1
    assert "is not installed" in capsys.readouterr().err
