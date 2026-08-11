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

import contextlib
import os
import sys
from types import SimpleNamespace

from proot_distro import dirfd
from proot_distro.commands.copy import command_copy
from proot_distro.commands.sync import command_sync
from proot_distro.paths import container_rootfs

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
