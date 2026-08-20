# Containment tests for the two halves of a build step that address the
# stage rootfs after deciding what to touch: the COPY/ADD materialiser
# (which resolves a destination and then writes it) and the layer packer
# (which lists the tree and then reads it).
#
# Both used to name the entry at every step, so the answer they had
# computed was re-resolved by each call that followed it. A process an
# earlier RUN left running is enough to exploit that — off Termux nothing
# kills one, since --kill-on-exit is a Termux-only proot extension — and
# on Termux the stage tree lives under the $TERMUX_PREFIX that every
# non-isolated container has bound read-write.

import gzip
import os
import stat
import tarfile

import pytest

from _builders import file_map_entry
from proot_distro import dirfd
from proot_distro.helpers import layer_diff
from proot_distro.helpers.build_engine import copy_step
from proot_distro.helpers.build_engine.errors import BuildError


@pytest.fixture
def tree(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "secret").write_bytes(b"host secret\n")
    return rootfs, outside


def _file_entry(src, mode=0o644):
    return file_map_entry(src, mode=mode)


# --- COPY/ADD materialisation ----------------------------------------------

def test_materialise_refuses_a_parent_that_is_a_symlink_now(tree, monkeypatch):
    # Stand in for the race: the resolve says <rootfs>/etc, and by the
    # time the write happens that name is a link out of the rootfs.
    rootfs, outside = tree
    os.symlink(str(outside), str(rootfs / "etc"))
    monkeypatch.setattr(copy_step, "safe_resolve_parts",
                        lambda root, parts: ["etc"])

    payload = rootfs.parent / "payload"
    payload.write_bytes(b"pwned\n")
    with pytest.raises(BuildError) as exc:
        copy_step._materialise_files(str(rootfs), {
            "etc/passwd": _file_entry(payload),
        })
    assert "not a directory inside it" in str(exc.value)
    assert not (outside / "passwd").exists()


def test_materialise_refuses_a_file_standing_in_for_a_parent(tree):
    rootfs, _outside = tree
    (rootfs / "etc").write_text("not a directory")
    payload = rootfs.parent / "payload"
    payload.write_bytes(b"x")

    with pytest.raises(BuildError):
        copy_step._materialise_files(str(rootfs), {
            "etc/passwd": _file_entry(payload),
        })


def test_materialise_does_not_write_through_a_hardlink(tree):
    # O_NOFOLLOW cannot tell a hardlink from an ordinary entry: it *is*
    # the file, under a second name. Writing has to create a new inode.
    rootfs, outside = tree
    victim = outside / "secret"
    os.link(str(victim), str(rootfs / "x"))
    payload = rootfs.parent / "payload"
    payload.write_bytes(b"pwned\n")

    copy_step._materialise_files(str(rootfs), {"x": _file_entry(payload)})

    assert victim.read_bytes() == b"host secret\n"
    assert (rootfs / "x").read_bytes() == b"pwned\n"
    assert os.lstat(str(rootfs / "x")).st_nlink == 1


def test_materialise_applies_the_recorded_mode_exactly(tree):
    # open(..., mode) is masked by the umask; the layer records 0666 and
    # the tree has to agree with it.
    rootfs, _outside = tree
    payload = rootfs.parent / "payload"
    payload.write_bytes(b"x")

    copy_step._materialise_files(
        str(rootfs), {"f": _file_entry(payload, mode=0o666)})

    assert stat.S_IMODE(os.lstat(str(rootfs / "f")).st_mode) == 0o666


def test_materialise_symlink_replaces_what_is_in_the_way(tree):
    rootfs, outside = tree
    (rootfs / "link").write_text("in the way")

    copy_step._materialise_files(str(rootfs), {
        "link": {"kind": "symlink", "target": "/elsewhere", "mode": 0o777,
                 "uid": 0, "gid": 0, "mtime": 0},
    })

    assert os.readlink(str(rootfs / "link")) == "/elsewhere"


def test_materialise_writes_an_ordinary_tree(tree):
    rootfs, _outside = tree
    payload = rootfs.parent / "payload"
    payload.write_bytes(b"content\n")

    copy_step._materialise_files(str(rootfs), {
        "opt": {"kind": "dir", "mode": 0o755, "uid": 0, "gid": 0, "mtime": 0},
        "opt/app": {"kind": "dir", "mode": 0o700, "uid": 0, "gid": 0,
                    "mtime": 0},
        "opt/app/run": _file_entry(payload, mode=0o755),
    })

    assert (rootfs / "opt" / "app" / "run").read_bytes() == b"content\n"
    assert stat.S_IMODE((rootfs / "opt" / "app").stat().st_mode) == 0o700
    assert stat.S_IMODE(
        (rootfs / "opt" / "app" / "run").stat().st_mode) == 0o755


# --- the layer packer ------------------------------------------------------

def _members(path):
    with tarfile.open(path, "r:gz") as tf:
        return {m.name: (m, tf.extractfile(m).read() if m.isreg() else None)
                for m in tf.getmembers()}


def test_parent_fds_refuses_a_symlinked_component(tree):
    rootfs, outside = tree
    os.symlink(str(outside), str(rootfs / "a"))
    parents = layer_diff._ParentFds(str(rootfs))
    try:
        assert parents.open("a") is None
        assert parents.open("") is not None
    finally:
        parents.close()


def test_parent_fds_reuses_the_cached_parent(tree):
    rootfs, _outside = tree
    (rootfs / "a" / "b").mkdir(parents=True)
    parents = layer_diff._ParentFds(str(rootfs))
    try:
        first = parents.open("a/b")
        assert first is not None
        assert parents.open("a/b") == first
        assert parents.open("a") != first
    finally:
        parents.close()


def test_layer_omits_an_entry_under_a_symlinked_parent(tree, tmp_path):
    rootfs, outside = tree
    # The tree the snapshot saw: a real directory holding a real file.
    (rootfs / "a").mkdir()
    (rootfs / "a" / "secret").write_bytes(b"image content\n")
    paths = sorted(layer_diff.snapshot(str(rootfs)))
    assert "a/secret" in paths

    # What it is by the time the layer is packed.
    os.unlink(str(rootfs / "a" / "secret"))
    os.rmdir(str(rootfs / "a"))
    os.symlink(str(outside), str(rootfs / "a"))

    out = tmp_path / "layer.tar.gz"
    layer_diff.write_layer_tar(str(rootfs), paths, [], str(out))

    members = _members(str(out))
    assert "a/secret" not in members
    assert b"host secret" not in gzip.decompress(out.read_bytes())


def test_layer_packs_an_ordinary_tree(tree, tmp_path):
    rootfs, _outside = tree
    (rootfs / "a").mkdir()
    (rootfs / "a" / "f").write_bytes(b"image content\n")
    os.symlink("f", str(rootfs / "a" / "l"))
    paths = sorted(layer_diff.snapshot(str(rootfs)))

    out = tmp_path / "layer.tar.gz"
    layer_diff.write_layer_tar(str(rootfs), paths, [], str(out))

    members = _members(str(out))
    assert members["a/f"][1] == b"image content\n"
    assert members["a"][0].isdir()
    assert members["a/l"][0].issym()
    assert members["a/l"][0].linkname == "f"


# --- the snapshot ----------------------------------------------------------

def test_snapshot_does_not_descend_a_symlinked_directory(tree):
    rootfs, outside = tree
    os.symlink(str(outside), str(rootfs / "a"))

    snap = layer_diff.snapshot(str(rootfs))

    assert snap["a"][0] == "symlink"
    assert not any(k.startswith("a/") for k in snap)


def test_snapshot_skips_special_files(tree):
    rootfs, _outside = tree
    os.mkfifo(str(rootfs / "pipe"))
    (rootfs / "f").write_bytes(b"x")

    snap = layer_diff.snapshot(str(rootfs))

    assert "pipe" not in snap
    assert snap["f"][0] == "file"


def test_snapshot_fingerprints_content(tree):
    rootfs, _outside = tree
    (rootfs / "f").write_bytes(b"one")
    first = layer_diff.snapshot(str(rootfs))
    # Same size and mtime, different bytes: only the CRC can tell.
    st = os.stat(str(rootfs / "f"))
    (rootfs / "f").write_bytes(b"two")
    os.utime(str(rootfs / "f"), ns=(st.st_atime_ns, st.st_mtime_ns))
    second = layer_diff.snapshot(str(rootfs))

    assert first["f"] != second["f"]
    assert layer_diff.diff_snapshots(first, second)[1] == ["f"]


def test_snapshot_skips_the_l2s_store(tree):
    rootfs, _outside = tree
    (rootfs / ".l2s").mkdir()
    (rootfs / ".l2s" / "backing").write_bytes(b"x")
    (rootfs / "sub").mkdir()
    (rootfs / "sub" / ".l2s").mkdir()

    snap = layer_diff.snapshot(str(rootfs))

    assert ".l2s" not in snap
    assert not any(k.startswith(".l2s/") for k in snap)
    # Only the one at the rootfs root is proot's.
    assert "sub/.l2s" in snap


def test_snapshot_handles_a_deep_tree(tree):
    rootfs, _outside = tree
    fd = os.open(str(rootfs), os.O_RDONLY | os.O_DIRECTORY)
    try:
        for _ in range(1200):
            os.mkdir("d", dir_fd=fd)
            nxt = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = nxt
    finally:
        os.close(fd)

    try:
        snap = layer_diff.snapshot(str(rootfs))
        assert "/".join(["d"] * 1200) in snap
    finally:
        # pytest's own tmp-dir reaper is shutil.rmtree, which recurses;
        # this tree is deeper than the interpreter's limit on purpose.
        dirfd.remove_tree(str(rootfs / "d"))
