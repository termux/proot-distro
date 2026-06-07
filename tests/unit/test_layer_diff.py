# Tests for proot_distro.helpers.layer_diff — rootfs snapshot/diff and the
# streaming OCI layer writers (round-tripped through apply_layer).

import os
import tarfile

from proot_distro.helpers import layer_diff
from proot_distro.helpers.docker.layers import apply_layer


def _write(path, data=b"x"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(data)


def test_snapshot_kinds(tmp_path):
    root = str(tmp_path / "r")
    _write(os.path.join(root, "dir", "f.txt"), b"hello")
    os.symlink("f.txt", os.path.join(root, "dir", "link"))
    snap = layer_diff.snapshot(root)
    assert snap["dir"][0] == "dir"
    assert snap["dir/f.txt"][0] == "file"
    assert snap["dir/f.txt"][1] == 5  # size
    assert snap["dir/link"] == ("symlink", "f.txt")


def test_snapshot_skips_l2s(tmp_path):
    root = str(tmp_path / "r")
    _write(os.path.join(root, ".l2s", "backing0001"), b"d")
    _write(os.path.join(root, "etc", "hostname"), b"h")
    snap = layer_diff.snapshot(root)
    assert "etc/hostname" in snap
    assert ".l2s" not in snap
    assert not any(k.startswith(".l2s") for k in snap)


def test_diff_snapshots():
    before = {"a": ("file", 1, 0, 0o644, 1), "b": ("file", 1, 0, 0o644, 1)}
    after = {"a": ("file", 2, 0, 0o644, 9), "c": ("file", 1, 0, 0o644, 1)}
    added, modified, deleted = layer_diff.diff_snapshots(before, after)
    assert added == ["c"]
    assert modified == ["a"]
    assert deleted == ["b"]


def test_whiteout_paths():
    arcnames = layer_diff._whiteout_paths(["a/b", "c"], ["d", ""])
    assert "a/.wh.b" in arcnames
    assert ".wh.c" in arcnames
    assert "d/.wh..wh..opq" in arcnames
    assert ".wh..wh..opq" in arcnames


def test_write_layer_tar_roundtrip(tmp_path):
    root = str(tmp_path / "src")
    _write(os.path.join(root, "etc", "hostname"), b"guest\n")
    _write(os.path.join(root, "usr", "bin", "tool"), b"BINARY")
    os.symlink("hostname", os.path.join(root, "etc", "alias"))
    paths = ["etc", "etc/hostname", "etc/alias", "usr", "usr/bin", "usr/bin/tool"]

    out = str(tmp_path / "layer.tar.gz")
    digest, size, diff_id = layer_diff.write_layer_tar(root, paths, [], out)
    assert digest.startswith("sha256:")
    assert diff_id.startswith("sha256:")
    assert size > 0

    dest = str(tmp_path / "dest")
    os.makedirs(dest)
    apply_layer(out, dest)
    assert open(os.path.join(dest, "etc", "hostname"), "rb").read() == b"guest\n"
    assert open(os.path.join(dest, "usr", "bin", "tool"), "rb").read() == b"BINARY"
    assert os.readlink(os.path.join(dest, "etc", "alias")) == "hostname"


def test_write_files_layer_honors_chown(tmp_path):
    # write_files_layer preserves the entry's uid/gid — that is how
    # COPY --chown lands ownership in the layer (contrast with _add_entry,
    # which zeroes ownership for disk-snapshot layers).
    file_map = {
        "etc/conf": {"kind": "content", "data": b"cfg", "mode": 0o600,
                     "uid": 1000, "gid": 1000, "mtime": 0},
        "etc/link": {"kind": "symlink", "target": "conf", "uid": 1000,
                     "gid": 1000, "mtime": 0},
    }
    out = str(tmp_path / "files.tar.gz")
    digest, size, diff_id = layer_diff.write_files_layer(file_map, out)
    assert digest.startswith("sha256:")

    with tarfile.open(out, "r:gz") as tf:
        members = {m.name: m for m in tf.getmembers()}
    assert members["etc/conf"].uid == 1000
    assert members["etc/conf"].gid == 1000
    assert members["etc/link"].issym()
    assert members["etc/link"].linkname == "conf"


def test_write_layer_tar_zeroes_ownership(tmp_path):
    # Disk-snapshot layers strip ownership for portability.
    root = str(tmp_path / "src")
    _write(os.path.join(root, "etc", "conf"), b"cfg")
    out = str(tmp_path / "layer.tar.gz")
    layer_diff.write_layer_tar(root, ["etc", "etc/conf"], [], out)
    with tarfile.open(out, "r:gz") as tf:
        member = tf.getmember("etc/conf")
    assert member.uid == 0
    assert member.gid == 0
    assert member.uname == ""
    assert member.gname == ""


def test_write_files_layer_roundtrip(tmp_path):
    file_map = {
        "etc/conf": {"kind": "content", "data": b"cfg", "mode": 0o644},
        "etc/link": {"kind": "symlink", "target": "conf"},
    }
    out = str(tmp_path / "files.tar.gz")
    layer_diff.write_files_layer(file_map, out)
    dest = str(tmp_path / "dest")
    os.makedirs(dest)
    apply_layer(out, dest)
    assert open(os.path.join(dest, "etc", "conf"), "rb").read() == b"cfg"
    assert os.readlink(os.path.join(dest, "etc", "link")) == "conf"


def test_l2s_symlink_inlined_as_regular_file(tmp_path):
    # A proot link2symlink symlink is packed as its backing file's content.
    root = str(tmp_path / "src")
    backing = os.path.join(root, ".l2s", ".proot.l2s.thing0001")
    _write(backing, b"L2SDATA")
    os.makedirs(os.path.join(root, "app"))
    os.symlink("../.l2s/.proot.l2s.thing0001",
               os.path.join(root, "app", "link"))

    out = str(tmp_path / "layer.tar.gz")
    layer_diff.write_layer_tar(root, ["app/link"], [], out)

    with tarfile.open(out, "r:gz") as tf:
        member = tf.getmember("app/link")
        assert member.isreg()
        assert tf.extractfile(member).read() == b"L2SDATA"
