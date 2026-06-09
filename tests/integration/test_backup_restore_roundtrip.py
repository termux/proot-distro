# Integration tests for the backup -> restore round trip.

import os
import shutil
import stat
import sys
from types import SimpleNamespace

import pytest

from proot_distro.commands import backup as backup_mod
from proot_distro.commands.backup import command_backup
from proot_distro.commands.restore import command_restore
from proot_distro.paths import container_dir, container_manifest, container_rootfs


def _backup(name, out, compression=None):
    command_backup(SimpleNamespace(
        container_name=name, output=str(out),
        compression=compression, verbose=False,
    ))


def _restore(archive):
    command_restore(SimpleNamespace(archive=str(archive), verbose=False))


def test_roundtrip_preserves_tree(tmp_path, builders):
    manifest = builders.simple_image_manifest(env=["A=B"])
    builders.make_container("src", manifest=manifest)
    root = container_rootfs("src")
    # Add a regular file and a (non-l2s) symlink.
    os.makedirs(os.path.join(root, "var"), exist_ok=True)
    with open(os.path.join(root, "var", "data.txt"), "wb") as fh:
        fh.write(b"payload")
    with open(os.path.join(root, "etc", "hostname"), "wb") as fh:
        fh.write(b"guest\n")
    os.symlink("etc/hostname", os.path.join(root, "hnlink"))

    expected = builders.tree_snapshot(root)

    out = tmp_path / "bk.tar.gz"
    _backup("src", out)
    shutil.rmtree(container_dir("src"))
    _restore(out)

    assert builders.tree_snapshot(container_rootfs("src")) == expected
    # manifest.json survives the round trip.
    assert os.path.isfile(container_manifest("src"))


def test_roundtrip_inlines_l2s_symlink(tmp_path, builders):
    builders.make_container("l2sbox")
    root = container_rootfs("l2sbox")
    os.makedirs(os.path.join(root, ".l2s"), exist_ok=True)
    with open(os.path.join(root, ".l2s", ".proot.l2s.x0001"), "wb") as fh:
        fh.write(b"L2SDATA")
    os.makedirs(os.path.join(root, "app"), exist_ok=True)
    os.symlink("../.l2s/.proot.l2s.x0001", os.path.join(root, "app", "link"))

    out = tmp_path / "l2s.tar.gz"
    _backup("l2sbox", out)
    shutil.rmtree(container_dir("l2sbox"))
    _restore(out)

    restored = container_rootfs("l2sbox")
    link = os.path.join(restored, "app", "link")
    # The l2s symlink is materialised as a real file with the backing content.
    assert os.path.isfile(link) and not os.path.islink(link)
    assert open(link, "rb").read() == b"L2SDATA"
    # The internal .l2s store is not carried into the archive.
    assert not os.path.exists(os.path.join(restored, ".l2s"))


def test_roundtrip_restrictive_dir_mode_preserved(tmp_path, builders):
    # A directory with a restrictive (but still enterable) mode and its
    # contents must survive, and the mode must be restored — exercising the
    # deferred directory-mode machinery in both backup and restore.
    builders.make_container("rd")
    root = container_rootfs("rd")
    ro = os.path.join(root, "ro")
    os.makedirs(ro)
    with open(os.path.join(ro, "inside.txt"), "wb") as fh:
        fh.write(b"content")
    os.chmod(ro, 0o555)  # r-x, no write

    out = tmp_path / "rd.tar.gz"
    try:
        _backup("rd", out)
        os.chmod(ro, 0o755)  # widen original so we can wipe it
        shutil.rmtree(container_dir("rd"))
        _restore(out)

        restored = os.path.join(container_rootfs("rd"), "ro")
        assert open(os.path.join(restored, "inside.txt"), "rb").read() == b"content"
        assert stat.S_IMODE(os.lstat(restored).st_mode) == 0o555
    finally:
        # Re-widen so the autouse cleanup can delete the tree.
        restored = os.path.join(container_rootfs("rd"), "ro")
        if os.path.isdir(restored):
            os.chmod(restored, 0o755)


def test_restore_rootfs_less_archive_preserves_existing(tmp_path, builders):
    # A rootfs-less archive (manifest only) naming an installed container
    # must be rejected without disturbing what is already on disk — the
    # destructive steps are deferred until a rootfs member is seen.
    from _builders import make_tar

    manifest = builders.simple_image_manifest(env=["KEEP=1"])
    builders.make_container("keep", manifest=manifest)
    before_tree = builders.tree_snapshot(container_rootfs("keep"))
    before_manifest = open(container_manifest("keep")).read()

    arc = tmp_path / "noroot.tar"
    make_tar(str(arc), [
        {"name": "keep/manifest.json", "type": "file", "data": b'{"other":1}'},
    ])

    with pytest.raises(SystemExit) as exc:
        _restore(arc)
    assert exc.value.code == 1
    # Existing rootfs and manifest are byte-for-byte untouched.
    assert builders.tree_snapshot(container_rootfs("keep")) == before_tree
    assert open(container_manifest("keep")).read() == before_manifest


def test_restore_dangling_rootfs_preserves_existing(tmp_path, builders):
    # An archive whose only rootfs entries do not resolve (here a dangling
    # hardlink) must be rejected without clearing the installed container:
    # the destructive clear is deferred until a member actually materialises,
    # and the manifest is written only on success.
    from _builders import make_tar

    manifest = builders.simple_image_manifest(env=["KEEP=1"])
    builders.make_container("keep", manifest=manifest)
    before_tree = builders.tree_snapshot(container_rootfs("keep"))
    before_manifest = open(container_manifest("keep")).read()

    arc = tmp_path / "dangling.tar"
    make_tar(str(arc), [
        {"name": "keep/manifest.json", "type": "file", "data": b'{"other":1}'},
        {"name": "keep/rootfs/x", "type": "hardlink",
         "linkname": "../../../../etc/shadow"},
    ])

    with pytest.raises(SystemExit) as exc:
        _restore(arc)
    assert exc.value.code == 1
    # Installed rootfs and manifest are byte-for-byte untouched.
    assert builders.tree_snapshot(container_rootfs("keep")) == before_tree
    assert open(container_manifest("keep")).read() == before_manifest


def test_backup_refuses_tty_stdout(monkeypatch, builders, capsys):
    builders.make_container("box")

    class _TTY:
        def isatty(self):
            return True

    monkeypatch.setattr(backup_mod.sys, "stdout", _TTY())
    with pytest.raises(SystemExit) as exc:
        command_backup(SimpleNamespace(
            container_name="box", output=None, compression=None, verbose=False,
        ))
    assert exc.value.code == 1


def test_backup_missing_container(capsys):
    with pytest.raises(SystemExit) as exc:
        command_backup(SimpleNamespace(
            container_name="ghost", output="x.tar", compression=None,
            verbose=False,
        ))
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err
