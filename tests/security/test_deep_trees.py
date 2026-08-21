# How deep a directory tree goes is guest content like any other, and the
# walks in `copy` and `sync` used to be plain Python recursion: a tree a
# little over a thousand levels down — which a container can build in a
# second — exhausted the interpreter's own stack and ended the command in a
# RecursionError traceback. RecursionError is not an OSError, so neither
# command's net caught it.
#
# Every walk now carries its open directories on an explicit stack. These
# tests build a tree deeper than the recursion limit and check that each
# pass finishes, and that the descriptors it opened on the way are all
# closed again — the stack has to unwind its own fds, which recursion got
# from `finally` for free.
#
# `remove` (and `reset`, which shares its walk) recursed the same way, on a
# tree it deletes rather than reads.
#
# So did every shutil.rmtree() in the program — the cleanup paths in
# `install`, `build`, `restore`, `clear-cache` and the tar extractor's
# whiteout handling. Those all ran under an `except OSError` or an
# ignore_errors=True, neither of which catches a RecursionError, so a tree
# an image can ship in a second took the command down with a traceback
# rather than being cleaned up. They share dirfd.remove_tree() now.
#
# `backup` is here as a guard rather than a fix: it walked with os.walk(),
# which is iterative, and now walks with its own fd stack, which has to stay
# that way. Same for its fds, which os.walk() never held.

import contextlib
import errno
import os
import resource
import sys
import tarfile
from types import SimpleNamespace

import pytest

from proot_distro import dirfd
from proot_distro.commands.backup import command_backup
from proot_distro.commands.clear_cache import command_clear_cache
from proot_distro.commands.copy import command_copy
from proot_distro.commands.remove import command_remove
from proot_distro.commands.sync import command_sync
from proot_distro.constants import BASE_CACHE_DIR
from proot_distro.helpers import layer_diff
from _builders import extract_tar_into
from proot_distro.paths import container_dir, container_rootfs

# Comfortably past sys.getrecursionlimit()'s default of 1000, and past the
# two frames per level that copy_tree_at and _mirror_at each used to take.
DEPTH = 1200


def _copy(source, destination, **over):
    base = dict(source=source, destination=destination, verbose=False,
                move=False, recursive=False)
    base.update(over)
    command_copy(SimpleNamespace(**base))


def _sync(source, destination, **over):
    base = dict(source=source, destination=destination, verbose=False,
                checksum=False, delete=False)
    base.update(over)
    command_sync(SimpleNamespace(**base))


def _make_deep(base, depth=DEPTH):
    """Build base/d/d/d/... with a file at the bottom, without recursing."""
    os.makedirs(base, exist_ok=True)
    fds = [os.open(base, os.O_RDONLY | os.O_DIRECTORY)]
    try:
        for _ in range(depth):
            os.mkdir("d", 0o755, dir_fd=fds[-1])
            fds.append(os.open("d", os.O_RDONLY | os.O_DIRECTORY,
                               dir_fd=fds[-1]))
        os.close(os.open("leaf", os.O_WRONLY | os.O_CREAT, 0o644,
                         dir_fd=fds[-1]))
    finally:
        for fd in fds:
            os.close(fd)


def _remove_deep(base):
    """Tear one down again; shutil.rmtree would hit the same limit."""
    if not os.path.isdir(base):
        return
    fds = [os.open(base, os.O_RDONLY | os.O_DIRECTORY)]
    try:
        while True:
            try:
                fds.append(os.open("d", os.O_RDONLY | os.O_DIRECTORY,
                                   dir_fd=fds[-1]))
            except OSError:
                break
        for fd in reversed(fds):
            for name in os.listdir(fd):
                try:
                    os.unlink(name, dir_fd=fd)
                except OSError:
                    with contextlib.suppress(OSError):
                        os.rmdir(name, dir_fd=fd)
    finally:
        for fd in fds:
            os.close(fd)
    with contextlib.suppress(OSError):
        os.rmdir(base)


def _depth_of(base):
    """How many levels of `d` are under *base*, counted without recursing."""
    fds = [os.open(base, os.O_RDONLY | os.O_DIRECTORY)]
    try:
        while True:
            try:
                fds.append(os.open("d", os.O_RDONLY | os.O_DIRECTORY,
                                   dir_fd=fds[-1]))
            except OSError:
                return len(fds) - 1
    finally:
        for fd in fds:
            os.close(fd)


def _open_fds():
    return set(os.listdir("/proc/self/fd"))


def test_copy_recursive_survives_a_tree_deeper_than_the_stack(tmp_path,
                                                              builders):
    builders.make_container("deep1")
    src = os.path.join(container_rootfs("deep1"), "deep")
    _make_deep(src)
    out = str(tmp_path / "out")
    before = _open_fds()
    try:
        assert DEPTH > sys.getrecursionlimit()
        _copy("deep1:/deep", out, recursive=True)
        assert _depth_of(out) == DEPTH
        assert len(_open_fds() - before) == 0
    finally:
        _remove_deep(src)
        _remove_deep(out)


def test_sync_survives_a_tree_deeper_than_the_stack(tmp_path, builders):
    builders.make_container("deep2")
    src = os.path.join(container_rootfs("deep2"), "deep")
    _make_deep(src)
    out = str(tmp_path / "out")
    before = _open_fds()
    try:
        _sync("deep2:/deep", out)
        assert _depth_of(out) == DEPTH
        assert len(_open_fds() - before) == 0
    finally:
        _remove_deep(src)
        _remove_deep(out)


def test_sync_delete_prunes_a_tree_deeper_than_the_stack(tmp_path, builders):
    """The prune passes and dirfd.rmtree_at recurse too."""
    builders.make_container("deep3")
    dest = os.path.join(container_rootfs("deep3"), "dest")
    _make_deep(dest)
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    before = _open_fds()
    try:
        _sync(str(src), "deep3:/dest", delete=True)
        assert sorted(os.listdir(dest)) == ["a.txt"]
        assert len(_open_fds() - before) == 0
    finally:
        _remove_deep(dest)


def test_move_across_devices_survives_a_deep_tree(tmp_path, builders,
                                                  monkeypatch):
    """The EXDEV fallback is copy_tree_at plus rmtree_at, both of them."""
    builders.make_container("deep4")
    rootfs = container_rootfs("deep4")
    src = os.path.join(rootfs, "deep")
    _make_deep(src)

    real_rename = os.rename

    def no_rename(*args, **kwargs):
        raise OSError(18, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", no_rename)
    before = _open_fds()
    try:
        _copy("deep4:/deep", "deep4:/moved", move=True)
        monkeypatch.setattr(os, "rename", real_rename)
        assert not os.path.exists(src)
        assert _depth_of(os.path.join(rootfs, "moved")) == DEPTH
        assert len(_open_fds() - before) == 0
    finally:
        monkeypatch.setattr(os, "rename", real_rename)
        _remove_deep(src)
        _remove_deep(os.path.join(rootfs, "moved"))


def test_copy_tree_at_closes_its_fds_when_a_walk_fails(tmp_path):
    """An error mid-descent must not leak the levels above it."""
    src = tmp_path / "src"
    (src / "a" / "b" / "c").mkdir(parents=True)
    (src / "a" / "b" / "c" / "f.txt").write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()

    real_mkdir = os.mkdir
    seen = []

    def failing_mkdir(name, *args, **kwargs):
        seen.append(name)
        if name == "c":
            raise OSError(28, "No space left on device")
        return real_mkdir(name, *args, **kwargs)

    src_fd = dirfd.opendir(str(src))
    dst_fd = dirfd.opendir(str(dst))
    before = _open_fds()
    try:
        os.mkdir = failing_mkdir
        try:
            dirfd.copy_tree_at(src_fd, dst_fd)
        except OSError as exc:
            assert exc.errno == 28
        else:
            raise AssertionError("expected the mkdir failure to propagate")
    finally:
        os.mkdir = real_mkdir
        os.close(dst_fd)
        os.close(src_fd)
    assert "c" in seen
    # The two fds closed above are the caller's; everything copy_tree_at
    # opened for itself must already be gone.
    assert len(_open_fds() - before) == 0


def test_backup_survives_a_tree_deeper_than_the_stack(tmp_path, builders):
    builders.make_container("deepbk")
    src = os.path.join(container_rootfs("deepbk"), "deep")
    _make_deep(src)
    out = tmp_path / "deep.tar"
    before = _open_fds()
    try:
        command_backup(SimpleNamespace(
            container_name="deepbk", output=str(out),
            compression=None, verbose=False,
        ))
        assert len(_open_fds() - before) == 0
        with tarfile.open(out) as tf:
            names = [m.name for m in tf.getmembers()]
        assert sum(1 for n in names if n.endswith("/leaf")) == 1
        assert sum(1 for n in names if n.endswith("/d")) == DEPTH
    finally:
        _remove_deep(src)


def test_remove_deletes_a_tree_deeper_than_the_stack(builders):
    builders.make_container("deeprm")
    deep = os.path.join(container_rootfs("deeprm"), "deep")
    _make_deep(deep)
    before = _open_fds()
    try:
        command_remove(SimpleNamespace(target="deeprm", verbose=False))
        assert not os.path.exists(container_dir("deeprm"))
        assert len(_open_fds() - before) == 0
    finally:
        _remove_deep(deep)


def test_remove_deletes_a_deep_tree_sealed_partway_down(builders):
    """The chmod that opens a sealed level has to work at depth too."""
    builders.make_container("deeprm2")
    deep = os.path.join(container_rootfs("deeprm2"), "deep")
    _make_deep(deep, depth=40)
    sealed = os.path.join(deep, *(["d"] * 20))
    os.chmod(sealed, 0o000)
    try:
        command_remove(SimpleNamespace(target="deeprm2", verbose=False))
        assert not os.path.exists(container_dir("deeprm2"))
    finally:
        if os.path.isdir(sealed):
            os.chmod(sealed, 0o755)
        _remove_deep(deep)


def test_whiteout_clears_a_tree_deeper_than_the_stack(tmp_path, builders):
    """A crafted image: a deep tree in one layer, a whiteout in the next."""
    root = tmp_path / "root"
    root.mkdir()
    _make_deep(str(root / "deep"))

    arc = tmp_path / "layer.tar"
    builders.make_tar(str(arc), [
        {"name": ".wh.deep", "type": "file", "data": b""},
        {"name": "after", "type": "file", "data": b"OK"},
    ])
    try:
        extract_tar_into(str(arc), str(root), handle_whiteouts=True)
        assert not (root / "deep").exists()
        # The layer kept being applied after the whiteout.
        assert (root / "after").read_bytes() == b"OK"
    finally:
        _remove_deep(str(root / "deep"))


def test_whiteout_clears_a_sealed_tree(tmp_path, builders):
    root = tmp_path / "root"
    sealed = root / "deep" / "sealed"
    sealed.mkdir(parents=True)
    (sealed / "inside").write_text("stale")
    sealed.chmod(0o000)

    arc = tmp_path / "layer.tar"
    builders.make_tar(str(arc), [
        {"name": ".wh.deep", "type": "file", "data": b""},
    ])
    try:
        extract_tar_into(str(arc), str(root), handle_whiteouts=True)
        assert not (root / "deep").exists()
    finally:
        if sealed.is_dir():
            sealed.chmod(0o755)


def test_clear_cache_removes_a_tree_deeper_than_the_stack(tmp_path):
    deep = os.path.join(BASE_CACHE_DIR, "oci_layers", "deep")
    _make_deep(deep)
    before = _open_fds()
    try:
        command_clear_cache(SimpleNamespace(
            verbose=False, orphan=False, build_cache=False))
        assert not os.path.exists(os.path.join(BASE_CACHE_DIR, "oci_layers"))
        assert len(_open_fds() - before) == 0
    finally:
        _remove_deep(deep)


# --- descriptors, not just stack frames ------------------------------------
#
# An explicit stack fixed the recursion; it left one descriptor open per
# level, which a deep tree exhausts just as surely. The soft limit is 1024
# on Android and on most distributions, so a tree a guest builds in a
# second took every one of these walks past it, and each answered
# differently and badly: `backup` left the deepest members out of the
# archive without a word, a build's layer came out missing whatever was
# below the limit, `clear-cache` reported the space it had *not*
# reclaimed, and `remove` could not delete the container at all.
#
# dirfd.Levels parks the levels beyond a budget and reopens each through
# its child's ".." as the walk unwinds, checked against the (device,
# inode) taken when it was parked.

# Deeper than any budget below, and deeper than the soft limits these
# tests impose, but cheap to build and to tear down.
FD_DEPTH = 400


def _make_chain(base, depth=FD_DEPTH, leaf="leaf"):
    """base/d/d/d/... with a file at the bottom, holding one fd at a time."""
    os.makedirs(base, exist_ok=True)
    fd = os.open(base, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for _ in range(depth):
            os.mkdir("d", 0o755, dir_fd=fd)
            nxt = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = nxt
        with open(leaf, "wb",
                  opener=lambda p, f: os.open(p, f, 0o644, dir_fd=fd)) as fh:
            fh.write(b"BOTTOM")
    finally:
        os.close(fd)


class _fd_limit:
    """Run with a soft descriptor limit far below the tree's depth."""

    def __init__(self, soft):
        self._soft = soft

    def __enter__(self):
        self._saved = resource.getrlimit(resource.RLIMIT_NOFILE)
        resource.setrlimit(resource.RLIMIT_NOFILE,
                           (self._soft, self._saved[1]))
        return self

    def __exit__(self, *_exc):
        resource.setrlimit(resource.RLIMIT_NOFILE, self._saved)
        return False


@pytest.fixture
def small_budget(monkeypatch):
    """Park after a handful of levels, so the tests need no huge trees."""
    monkeypatch.setattr(dirfd, "MAX_OPEN_LEVELS", 8)
    return 8


def _peak_fds(monkeypatch):
    """Record the descriptor count after every directory the walk opens."""
    peak = [len(os.listdir("/proc/self/fd"))]
    real = dirfd.opendir_at

    def watching(fd, name):
        opened = real(fd, name)
        peak[0] = max(peak[0], len(os.listdir("/proc/self/fd")))
        return opened

    monkeypatch.setattr(dirfd, "opendir_at", watching)
    return peak


def test_copy_of_a_deep_tree_holds_a_bounded_number_of_fds(tmp_path, builders,
                                                           monkeypatch):
    builders.make_container("fdcopy")
    src = os.path.join(container_rootfs("fdcopy"), "deep")
    _make_chain(src)
    out = str(tmp_path / "out")
    peak = _peak_fds(monkeypatch)
    try:
        _copy("fdcopy:/deep", out, recursive=True)
        assert _depth_of(out) == FD_DEPTH
        # Two descriptors per level, so the ceiling is the budget twice
        # over plus the handful the process already held.
        assert peak[0] < 2 * dirfd.MAX_OPEN_LEVELS + 40
    finally:
        _remove_deep(src)
        _remove_deep(out)


def test_sync_of_a_deep_tree_holds_a_bounded_number_of_fds(tmp_path, builders,
                                                           monkeypatch):
    builders.make_container("fdsync")
    src = os.path.join(container_rootfs("fdsync"), "deep")
    _make_chain(src)
    out = str(tmp_path / "out")
    peak = _peak_fds(monkeypatch)
    try:
        _sync("fdsync:/deep", out)
        assert _depth_of(out) == FD_DEPTH
        assert peak[0] < 2 * dirfd.MAX_OPEN_LEVELS + 40
    finally:
        _remove_deep(src)
        _remove_deep(out)


def test_backup_archives_every_level_under_an_fd_limit(tmp_path, builders,
                                                       small_budget):
    # The failure this replaces was silent: each directory the walk could
    # not open was skipped, so the archive simply stopped at whatever
    # depth the table ran out and reported success.
    builders.make_container("fdbk")
    src = os.path.join(container_rootfs("fdbk"), "deep")
    _make_chain(src)
    out = tmp_path / "deep.tar"
    try:
        with _fd_limit(96):
            command_backup(SimpleNamespace(
                container_name="fdbk", output=str(out),
                compression=None, verbose=False,
            ))
        with tarfile.open(out) as tf:
            names = [m.name for m in tf.getmembers()]
        assert sum(1 for n in names if n.endswith("/d")) == FD_DEPTH
        assert sum(1 for n in names if n.endswith("/leaf")) == 1
    finally:
        _remove_deep(src)


def test_snapshot_records_every_level_under_an_fd_limit(tmp_path,
                                                        small_budget):
    # What snapshot() misses, the layer misses: a build would have
    # published an image with everything below the limit missing.
    root = tmp_path / "rootfs"
    root.mkdir()
    _make_chain(str(root / "deep"))
    try:
        with _fd_limit(96):
            snap = layer_diff.snapshot(str(root))
        deepest = "deep/" + "/".join(["d"] * FD_DEPTH) + "/leaf"
        assert deepest in snap
    finally:
        _remove_deep(str(root / "deep"))


def test_clear_cache_removes_a_deep_tree_under_an_fd_limit(small_budget):
    deep = os.path.join(BASE_CACHE_DIR, "oci_layers", "deep")
    _make_chain(deep)
    try:
        with _fd_limit(96):
            command_clear_cache(SimpleNamespace(
                verbose=False, orphan=False, build_cache=False))
        assert not os.path.exists(os.path.join(BASE_CACHE_DIR, "oci_layers"))
    finally:
        _remove_deep(deep)


def test_remove_deletes_a_deep_tree_under_an_fd_limit(builders, small_budget):
    # The worst of them: a container that could not be removed at all.
    builders.make_container("fdrm")
    deep = os.path.join(container_rootfs("fdrm"), "deep")
    _make_chain(deep)
    try:
        with _fd_limit(96):
            command_remove(SimpleNamespace(target="fdrm", verbose=False))
        assert not os.path.exists(container_dir("fdrm"))
    finally:
        _remove_deep(deep)


# --- what ".." is allowed to be -------------------------------------------

def test_a_parked_level_is_refused_when_it_moved(tmp_path, monkeypatch):
    # Reopening through ".." asks the kernel for the directory's own
    # parent, so no name a guest plants can redirect it -- but a
    # directory it *moves* has a different parent, and following one
    # would take the walk out of the tree it was pointed at.
    root = tmp_path / "root"
    (root / "a" / "b" / "c").mkdir(parents=True)
    (root / "a" / "b" / "c" / "f").write_text("x")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()

    # With a budget of one, `a` is parked as soon as the walk reaches
    # `c`, and it is `b` the revive has to come back through.
    monkeypatch.setattr(dirfd, "MAX_OPEN_LEVELS", 1)
    root_fd = dirfd.opendir(str(root))
    moved = []

    real_listdir_at = dirfd.listdir_at

    def moving_listdir_at(fd):
        names = real_listdir_at(fd)
        if names == ["f"] and not moved:
            moved.append(True)
            os.rename(str(root / "a" / "b"), str(elsewhere / "b"))
        return names

    monkeypatch.setattr(dirfd, "listdir_at", moving_listdir_at)
    try:
        with pytest.raises(OSError) as exc:
            dirfd.count_tree_at(root_fd)
        assert exc.value.errno == errno.ESTALE
        assert moved
    finally:
        os.close(root_fd)


def test_levels_revives_what_it_parked(tmp_path):
    # The plain case: everything the walk parked comes back, and it counts
    # the whole tree rather than the part that fitted.
    root = tmp_path / "root"
    root.mkdir()
    _make_chain(str(root), depth=200)
    fd = dirfd.opendir(str(root))
    before = _open_fds()
    try:
        assert dirfd.count_tree_at(fd) == 1
    finally:
        os.close(fd)
        _remove_deep(str(root / "d"))
    assert len(_open_fds() - before) == 0
