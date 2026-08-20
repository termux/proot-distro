# Integration tests for the backup -> restore round trip.

import os
import shutil
import stat
import sys
import tarfile
from types import SimpleNamespace

import pytest

from proot_distro.commands import backup as backup_mod
from proot_distro.commands.backup import command_backup
from proot_distro.commands.restore import command_restore
from proot_distro.paths import container_dir, container_manifest, container_rootfs
from proot_distro.shm import shm_dir


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


def test_roundtrip_sealed_subtree_is_archived(tmp_path, builders):
    # _fix_permissions relaxes a chmod-000 directory so its contents can be
    # read. os.walk() listed a directory before handing it over, so it gave
    # up on one silently and the whole subtree stayed out of the archive;
    # the fd walk visits an entry before descending, so the chmod lands
    # first and the descent then succeeds.
    builders.make_container("sealed")
    root = container_rootfs("sealed")
    inner = os.path.join(root, "sealed")
    os.makedirs(inner)
    with open(os.path.join(inner, "inside.txt"), "wb") as fh:
        fh.write(b"hidden")
    os.chmod(inner, 0o000)

    out = tmp_path / "sealed.tar"
    try:
        _backup("sealed", out)
    finally:
        os.chmod(inner, 0o755)

    with tarfile.open(out) as tf:
        names = {m.name for m in tf.getmembers()}
    assert "sealed/rootfs/sealed" in names
    assert "sealed/rootfs/sealed/inside.txt" in names


def test_roundtrip_hardlinked_files_share_one_copy(tmp_path, builders):
    # tarfile's (dev, ino) table is what turns a second name for a file
    # already in the archive into a link member instead of a second copy,
    # and it is kept by gettarinfo() — which the archiver now calls off the
    # open descriptor rather than off the name.
    builders.make_container("hl")
    root = container_rootfs("hl")
    first = os.path.join(root, "etc", "a.txt")
    with open(first, "wb") as fh:
        fh.write(b"shared")
    os.link(first, os.path.join(root, "etc", "b.txt"))

    out = tmp_path / "hl.tar"
    _backup("hl", out)

    with tarfile.open(out) as tf:
        members = {m.name: m for m in tf.getmembers()}
    links = [m for m in members.values() if m.islnk()]
    assert len(links) == 1
    assert links[0].linkname in ("hl/rootfs/etc/a.txt", "hl/rootfs/etc/b.txt")

    shutil.rmtree(container_dir("hl"))
    _restore(out)
    restored = container_rootfs("hl")
    # restore copies the content rather than recreating the link.
    for name in ("a.txt", "b.txt"):
        with open(os.path.join(restored, "etc", name), "rb") as fh:
            assert fh.read() == b"shared"


def test_restore_writes_into_a_sealed_directory_member(tmp_path):
    # A directory member whose archived mode has no owner rwx has to be
    # widened while its own children are written and sealed again at the
    # end — the deferred-mode machinery, now replayed through the
    # descriptor walk rather than by path.
    from _builders import make_tar

    arc = tmp_path / "sealed.tar"
    make_tar(str(arc), [
        {"name": "box/rootfs/sealed", "type": "dir", "mode": 0o000},
        {"name": "box/rootfs/sealed/inside", "type": "file", "data": b"hidden"},
    ])
    _restore(arc)

    sealed = os.path.join(container_rootfs("box"), "sealed")
    try:
        assert stat.S_IMODE(os.lstat(sealed).st_mode) == 0o000
        os.chmod(sealed, 0o700)
        with open(os.path.join(sealed, "inside"), "rb") as fh:
            assert fh.read() == b"hidden"
    finally:
        os.chmod(sealed, 0o755)


def test_restore_clears_a_sealed_subtree_of_the_old_rootfs(tmp_path, builders):
    # The old rootfs is cleared before the archive is unpacked. That pass
    # used to be an os.walk() bottom-up loop with a shutil.rmtree() behind
    # it, and neither can get into a directory the guest chmod-000'ed: the
    # walk lists a directory before handing it over, and rmtree cannot
    # chmod. So a sealed subtree of the *previous* container survived into
    # the "restored" one, mixing stale content into it.
    builders.make_container("stale")
    out = tmp_path / "stale.tar"
    _backup("stale", out)

    sealed = os.path.join(container_rootfs("stale"), "sealed")
    os.makedirs(sealed)
    with open(os.path.join(sealed, "leftover"), "wb") as fh:
        fh.write(b"STALE")
    os.chmod(sealed, 0o000)

    try:
        _restore(out)
    finally:
        if os.path.isdir(sealed):
            os.chmod(sealed, 0o755)

    assert not os.path.exists(sealed)


def test_restore_clears_the_shm_store(tmp_path, builders):
    # No archive carries one — it is the container's own scratch, next to
    # the rootfs rather than inside it — so restoring over a container
    # must not leave the previous one's /dev/shm content behind.
    builders.make_container("shmrst")
    out = tmp_path / "shmrst.tar"
    _backup("shmrst", out)

    store = shm_dir(container_rootfs("shmrst"))
    os.makedirs(store, exist_ok=True)
    with open(os.path.join(store, "stale"), "wb") as fh:
        fh.write(b"OLD")

    _restore(out)

    assert not os.path.exists(store)
    assert os.path.isdir(container_rootfs("shmrst"))


def test_restore_leaves_no_shm_store_when_there_was_none(tmp_path, builders):
    builders.make_container("shmrst2")
    out = tmp_path / "shmrst2.tar"
    _backup("shmrst2", out)

    _restore(out)

    assert not os.path.exists(shm_dir(container_rootfs("shmrst2")))


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
