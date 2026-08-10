# Tests for proot_distro.dirfd — the openat(2) walking primitives that
# `copy` and `sync` are built on.

import errno
import os
import stat

import pytest

from proot_distro import dirfd


def _fd(path):
    return dirfd.opendir(str(path))


# ----- opening ------------------------------------------------------------

def test_opendir_at_refuses_symlinked_directory(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(str(real), tmp_path / "link")
    fd = _fd(tmp_path)
    try:
        with pytest.raises(OSError) as exc:
            dirfd.opendir_at(fd, "link")
        # Linux reports O_NOFOLLOW|O_DIRECTORY on a symlink as ENOTDIR.
        assert dirfd.is_refusal(exc.value)
        assert exc.value.errno in (errno.ELOOP, errno.ENOTDIR)
    finally:
        os.close(fd)


def test_reopen_returns_same_directory(tmp_path):
    (tmp_path / "sub").mkdir()
    fd = os.open(str(tmp_path), (getattr(os, "O_PATH", 0) or os.O_RDONLY)
                 | os.O_DIRECTORY)
    try:
        # An O_PATH fd cannot be scanned; reopen() makes it readable.
        again = dirfd.reopen(fd)
        try:
            assert dirfd.listdir_at(again) == ["sub"]
        finally:
            os.close(again)
        child = dirfd.reopen(fd, "sub")
        try:
            assert os.path.samestat(os.fstat(child),
                                    os.stat(tmp_path / "sub"))
        finally:
            os.close(child)
    finally:
        os.close(fd)


def test_open_file_at_refuses_symlink(tmp_path):
    victim = tmp_path / "victim"
    victim.write_text("keep")
    os.symlink(str(victim), tmp_path / "link")
    fd = _fd(tmp_path)
    try:
        with pytest.raises(OSError) as exc:
            dirfd.open_file_at(fd, "link", os.O_WRONLY | os.O_TRUNC)
        assert exc.value.errno == errno.ELOOP
    finally:
        os.close(fd)
    assert victim.read_text() == "keep"


def test_listdir_at_is_sorted(tmp_path):
    for name in ("c", "a", "b"):
        (tmp_path / name).write_text("")
    fd = _fd(tmp_path)
    try:
        assert dirfd.listdir_at(fd) == ["a", "b", "c"]
    finally:
        os.close(fd)


def test_exists_at_sees_dangling_symlink(tmp_path):
    os.symlink(str(tmp_path / "nothing"), tmp_path / "dangling")
    fd = _fd(tmp_path)
    try:
        assert dirfd.exists_at(fd, "dangling")
        assert not dirfd.exists_at(fd, "absent")
    finally:
        os.close(fd)


# ----- copying ------------------------------------------------------------

def test_copy_file_at_preserves_mode_and_mtime(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    dst = tmp_path / "dst"
    dst.mkdir()
    f = src / "f.sh"
    f.write_text("#!/bin/sh\n")
    os.chmod(f, 0o750)
    os.utime(f, (1234567, 1234567))

    sfd, dfd = _fd(src), _fd(dst)
    try:
        dirfd.copy_file_at(sfd, "f.sh", dfd, "f.sh")
    finally:
        os.close(sfd)
        os.close(dfd)

    st = os.stat(dst / "f.sh")
    assert stat.S_IMODE(st.st_mode) == 0o750
    assert int(st.st_mtime) == 1234567
    assert (dst / "f.sh").read_text() == "#!/bin/sh\n"


def test_copy_tree_at_mirrors_dirs_files_and_symlinks(tmp_path):
    src = tmp_path / "src"
    (src / "a" / "b").mkdir(parents=True)
    (src / "top.txt").write_text("T")
    (src / "a" / "mid.txt").write_text("M")
    (src / "a" / "b" / "deep.txt").write_text("D")
    os.symlink("../top.txt", src / "a" / "rel")
    os.symlink("/etc/passwd", src / "abs")
    os.chmod(src / "a", 0o751)

    dst = tmp_path / "dst"
    dst.mkdir()
    seen, skipped = [], []
    sfd, dfd = _fd(src), _fd(dst)
    try:
        dirfd.copy_tree_at(sfd, dfd, on_entry=seen.append,
                           on_skip=skipped.append)
    finally:
        os.close(sfd)
        os.close(dfd)

    assert (dst / "top.txt").read_text() == "T"
    assert (dst / "a" / "b" / "deep.txt").read_text() == "D"
    # Symlinks are recreated verbatim, never resolved.
    assert os.readlink(dst / "abs") == "/etc/passwd"
    assert os.readlink(dst / "a" / "rel") == "../top.txt"
    assert stat.S_IMODE(os.stat(dst / "a").st_mode) == 0o751
    assert sorted(seen) == ["a/b/deep.txt", "a/mid.txt", "a/rel", "abs",
                            "top.txt"]
    assert skipped == []


def test_copy_tree_at_does_not_follow_symlinked_dirs(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("S")
    src = tmp_path / "src"
    src.mkdir()
    os.symlink(str(outside), src / "link")

    dst = tmp_path / "dst"
    dst.mkdir()
    sfd, dfd = _fd(src), _fd(dst)
    try:
        dirfd.copy_tree_at(sfd, dfd)
    finally:
        os.close(sfd)
        os.close(dfd)

    # The link is recreated as a link and never descended into, so the
    # target's contents are not pulled into the destination tree.
    assert os.path.islink(dst / "link")
    assert sorted(os.listdir(dst)) == ["link"]


def test_copy_tree_at_skips_special_files(tmp_path):
    src = tmp_path / "src"
    src.mkdir()
    (src / "ok.txt").write_text("K")
    os.mkfifo(src / "pipe")
    dst = tmp_path / "dst"
    dst.mkdir()

    skipped = []
    sfd, dfd = _fd(src), _fd(dst)
    try:
        dirfd.copy_tree_at(sfd, dfd, on_skip=skipped.append)
    finally:
        os.close(sfd)
        os.close(dfd)

    assert skipped == ["pipe"]
    assert sorted(os.listdir(dst)) == ["ok.txt"]


# ----- removing -----------------------------------------------------------

def test_rmtree_at_removes_nested_tree(tmp_path):
    tree = tmp_path / "tree"
    (tree / "a" / "b").mkdir(parents=True)
    (tree / "a" / "b" / "f.txt").write_text("x")
    fd = _fd(tmp_path)
    try:
        dirfd.rmtree_at(fd, "tree")
    finally:
        os.close(fd)
    assert not tree.exists()


def test_rmtree_at_unlinks_symlinks_without_following(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("K")
    tree = tmp_path / "tree"
    tree.mkdir()
    os.symlink(str(outside), tree / "link")

    fd = _fd(tmp_path)
    try:
        dirfd.rmtree_at(fd, "tree")
    finally:
        os.close(fd)

    assert not tree.exists()
    assert (outside / "keep.txt").read_text() == "K"


def test_rmtree_at_force_handles_unwritable_dir(tmp_path):
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f.txt").write_text("x")
    os.chmod(tree / "sub", 0o500)

    fd = _fd(tmp_path)
    try:
        with pytest.raises(OSError):
            dirfd.rmtree_at(fd, "tree")
        dirfd.rmtree_at(fd, "tree", force=True)
    finally:
        os.close(fd)
    assert not tree.exists()


def test_rmtree_at_missing_entry_is_noop(tmp_path):
    fd = _fd(tmp_path)
    try:
        dirfd.rmtree_at(fd, "absent")
    finally:
        os.close(fd)
