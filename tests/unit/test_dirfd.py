# Tests for proot_distro.dirfd — the openat(2) walking primitives that
# `copy` and `sync` are built on.

import contextlib
import errno
import os
import signal
import stat

import pytest

from proot_distro import dirfd


def _fd(path):
    return dirfd.opendir(str(path))


class _Blocked(Exception):
    """Raised when a call under _deadline() did not return in time.

    Deliberately not an OSError: the code under test catches OSError and
    turns it into a tidy `sys.exit(1)`, which would make a blocked open
    indistinguishable from a clean refusal and let the regression pass.
    """


@contextlib.contextmanager
def _deadline(seconds=5):
    """Turn a blocked syscall into a failure rather than a hung suite."""
    def fire(signum, frame):
        raise _Blocked("call did not return")

    previous = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


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


def test_open_new_at_replaces_a_name_rather_than_writing_into_it(tmp_path):
    """A hardlink is invisible to O_NOFOLLOW: it *is* the file, twice named.

    A guest that links a host file into its rootfs under the name a transfer
    is about to write leaves nothing to refuse — an O_TRUNC write lands on
    the host's inode. O_EXCL plus an unlink removes the *name* instead, so
    the other link keeps its content.
    """
    victim = tmp_path / "victim"
    victim.write_text("HOST DATA")
    box = tmp_path / "box"
    box.mkdir()
    os.link(victim, box / "planted")

    fd = _fd(box)
    try:
        nfd, _ = dirfd.open_new_at(fd, "planted", 0o644)
        os.write(nfd, b"payload")
        os.close(nfd)
    finally:
        os.close(fd)

    assert victim.read_text() == "HOST DATA"
    assert os.stat(victim).st_nlink == 1
    assert (box / "planted").read_text() == "payload"


def test_copy_file_at_never_writes_through_a_hardlink(tmp_path):
    src = tmp_path / "payload"
    src.write_text("payload")
    victim = tmp_path / "victim"
    victim.write_text("HOST DATA")
    box = tmp_path / "box"
    box.mkdir()
    os.link(victim, box / "dest")

    sfd, dfd = _fd(tmp_path), _fd(box)
    try:
        # Both modes: a tree copy creates, an endpoint copy replaces.
        dirfd.copy_file_at(sfd, "payload", dfd, "dest")
        assert victim.read_text() == "HOST DATA"
        os.link(victim, box / "dest2")
        dirfd.copy_file_at(sfd, "payload", dfd, "dest2", replace=True)
    finally:
        os.close(sfd)
        os.close(dfd)

    assert victim.read_text() == "HOST DATA"
    assert (box / "dest").read_text() == "payload"
    assert (box / "dest2").read_text() == "payload"


@pytest.mark.parametrize("plant", ["symlink", "fifo", "dir"])
def test_copy_file_at_replace_refuses_a_non_regular_destination(tmp_path,
                                                                plant):
    """Refused rather than replaced: nothing legitimate can be standing there.

    A plain copy has already followed whatever link the destination named, so
    one there now was planted since — and a pipe or a device is not something
    to overwrite without saying so.
    """
    src = tmp_path / "payload"
    src.write_text("payload")
    box = tmp_path / "box"
    box.mkdir()
    outside = tmp_path / "outside"
    outside.write_text("HOST DATA")
    if plant == "symlink":
        os.symlink(str(outside), box / "dest")
    elif plant == "fifo":
        os.mkfifo(box / "dest")
    else:
        (box / "dest").mkdir()

    sfd, dfd = _fd(tmp_path), _fd(box)
    try:
        with _deadline():
            with pytest.raises(OSError):
                dirfd.copy_file_at(sfd, "payload", dfd, "dest", replace=True)
    finally:
        os.close(sfd)
        os.close(dfd)

    assert outside.read_text() == "HOST DATA"
    assert not (box / ("dest" + dirfd.TMP_SUFFIX)).exists()


def test_copy_file_at_replace_leaves_the_old_file_on_failure(tmp_path,
                                                             monkeypatch):
    """The rename is the commit point, so a failed copy changes nothing."""
    src = tmp_path / "payload"
    src.write_text("payload")
    box = tmp_path / "box"
    box.mkdir()
    (box / "dest").write_text("ORIGINAL")

    def boom(*_a, **_kw):
        raise OSError(errno.EIO, "disk on fire")

    monkeypatch.setattr(dirfd, "copy_data", boom)
    sfd, dfd = _fd(tmp_path), _fd(box)
    try:
        with pytest.raises(OSError):
            dirfd.copy_file_at(sfd, "payload", dfd, "dest", replace=True)
    finally:
        os.close(sfd)
        os.close(dfd)

    assert (box / "dest").read_text() == "ORIGINAL"
    assert sorted(os.listdir(box)) == ["dest"]      # no temp left behind


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


# ----- open_regular_at ----------------------------------------------------

def test_open_regular_at_returns_fd_and_stat(tmp_path):
    (tmp_path / "f").write_text("data")
    fd = _fd(tmp_path)
    try:
        ffd, st = dirfd.open_regular_at(fd, "f", os.O_RDONLY)
        try:
            assert st.st_size == 4
            assert os.read(ffd, 4) == b"data"
        finally:
            os.close(ffd)
    finally:
        os.close(fd)


def test_open_regular_at_refuses_symlink(tmp_path):
    (tmp_path / "real").write_text("x")
    os.symlink("real", tmp_path / "link")
    fd = _fd(tmp_path)
    try:
        with pytest.raises(OSError) as exc:
            dirfd.open_regular_at(fd, "link", os.O_RDONLY)
        assert exc.value.errno == errno.ELOOP
    finally:
        os.close(fd)


def test_open_regular_at_refuses_fifo_without_blocking(tmp_path):
    """A planted pipe must be refused, not waited on.

    Both directions need covering, and for different reasons: opening a FIFO
    for writing blocks until a reader appears, which O_NONBLOCK turns into
    ENXIO, while opening one for reading succeeds straight away and only the
    type check catches it. With neither, a `copy` whose endpoint a guest had
    replaced with a pipe hung for as long as the user left it running.
    """
    os.mkfifo(tmp_path / "pipe")
    fd = _fd(tmp_path)
    try:
        with _deadline():
            with pytest.raises(OSError) as wr:
                dirfd.open_regular_at(fd, "pipe",
                                      os.O_WRONLY | os.O_CREAT | os.O_TRUNC)
            assert wr.value.errno == errno.ENXIO
            with pytest.raises(OSError) as rd:
                dirfd.open_regular_at(fd, "pipe", os.O_RDONLY)
            assert rd.value.errno == errno.EINVAL
    finally:
        os.close(fd)


def test_open_regular_at_refuses_a_directory(tmp_path):
    (tmp_path / "d").mkdir()
    fd = _fd(tmp_path)
    try:
        with pytest.raises(OSError):
            dirfd.open_regular_at(fd, "d", os.O_RDONLY)
    finally:
        os.close(fd)


# ----- chmod through descriptors ------------------------------------------

@pytest.mark.skipif(not getattr(os, "O_PATH", 0), reason="needs O_PATH")
def test_make_writable_works_on_an_o_path_fd(tmp_path):
    """paths.pin_path hands out O_PATH fds, where fchmod is EBADF.

    Every caller wraps the chmod in `except OSError: pass`, so the failure
    was silent and the recovery it guards simply never happened at a copy or
    sync endpoint: `sync <file> box:/unwritable/f` reported the permission
    error that make_writable was called to clear.
    """
    d = tmp_path / "d"
    d.mkdir()
    os.chmod(d, 0o500)

    fd = os.open(str(d), dirfd._O_PATH_DIR)
    try:
        with pytest.raises(OSError) as exc:
            os.fchmod(fd, 0o700)        # the call that used to be swallowed
        assert exc.value.errno == errno.EBADF
        dirfd.make_writable(fd)
    finally:
        os.close(fd)

    assert stat.S_IMODE(os.stat(d).st_mode) & stat.S_IRWXU == stat.S_IRWXU


def test_rmtree_at_force_removes_unreadable_dir(tmp_path):
    """Mode 0000 is what the force path exists for: EACCES, not EPERM.

    An unwritable-but-readable directory (0500) never reaches the retry,
    so it does not exercise this at all.
    """
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "f.txt").write_text("x")
    os.chmod(tree / "sub", 0o000)

    fd = _fd(tmp_path)
    try:
        with pytest.raises(OSError):
            dirfd.rmtree_at(fd, "tree")
        dirfd.rmtree_at(fd, "tree", force=True)
    finally:
        os.close(fd)
    assert not tree.exists()
