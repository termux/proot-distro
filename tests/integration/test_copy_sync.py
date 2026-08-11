# Integration tests for `command_copy` and `command_sync` between host paths
# and container `name:path` specs.

import os
import stat
from types import SimpleNamespace

import pytest

from proot_distro.commands.copy import command_copy
from proot_distro.commands.sync import command_sync
from proot_distro.paths import container_rootfs


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


def test_copy_host_to_container(tmp_path, builders):
    builders.make_container("box")
    host = tmp_path / "h.txt"
    host.write_text("DATA")
    _copy(str(host), "box:/root/h.txt")
    assert open(os.path.join(container_rootfs("box"), "root", "h.txt")).read() == "DATA"


def test_copy_container_to_host(tmp_path, builders):
    builders.make_container("box")
    dest = tmp_path / "out.txt"
    _copy("box:/etc/passwd", str(dest))
    assert "root:x:0:0" in dest.read_text()


def test_copy_recursive_dir(tmp_path, builders):
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "a.txt").write_text("a")
    (src / "sub" / "b.txt").write_text("b")
    _copy(str(src), "box:/data", recursive=True)
    root = container_rootfs("box")
    assert open(os.path.join(root, "data", "a.txt")).read() == "a"
    assert open(os.path.join(root, "data", "sub", "b.txt")).read() == "b"


def test_copy_recursive_preserves_symlinks_and_modes(tmp_path, builders):
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "b.txt").write_text("b")
    os.chmod(src / "sub", 0o751)
    os.chmod(src / "sub" / "b.txt", 0o640)
    os.utime(src / "sub" / "b.txt", (1500000, 1500000))
    os.symlink("sub/b.txt", src / "rel")
    os.symlink("/etc/passwd", src / "abs")

    _copy(str(src), "box:/data", recursive=True)

    root = os.path.join(container_rootfs("box"), "data")
    # Symlinks are recreated verbatim, never resolved or followed.
    assert os.readlink(os.path.join(root, "rel")) == "sub/b.txt"
    assert os.readlink(os.path.join(root, "abs")) == "/etc/passwd"
    st_dir = os.stat(os.path.join(root, "sub"))
    st_file = os.stat(os.path.join(root, "sub", "b.txt"))
    assert stat.S_IMODE(st_dir.st_mode) == 0o751
    assert stat.S_IMODE(st_file.st_mode) == 0o640
    assert int(st_file.st_mtime) == 1500000


def test_copy_recursive_preserves_umask_sensitive_dir_modes(tmp_path, builders):
    """Directory modes come from a chmod, not from mkdir's masked mode.

    0o751 (above) survives any ordinary umask untouched, so it cannot
    show whether the mode was applied or merely passed to mkdir. These
    two lose bits to a 022 umask, and 1777 also carries a sticky bit.
    """
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "wide").mkdir(parents=True)
    (src / "tmp").mkdir()
    (src / "wide" / "f.txt").write_text("f")
    os.chmod(src / "wide", 0o777)
    os.chmod(src / "tmp", 0o1777)

    _copy(str(src), "box:/data", recursive=True)

    root = os.path.join(container_rootfs("box"), "data")
    assert stat.S_IMODE(os.stat(os.path.join(root, "wide")).st_mode) == 0o777
    assert stat.S_IMODE(os.stat(os.path.join(root, "tmp")).st_mode) == 0o1777


def test_copy_recursive_through_readonly_source_dir(tmp_path, builders):
    """A source directory with no write bit must not reject its own contents.

    The destination is created writable and sealed afterwards; creating
    it with the source mode up front made the copy abort with EACCES on
    the first file written into it.
    """
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "ro").mkdir(parents=True)
    (src / "ro" / "f.txt").write_text("DATA")
    os.chmod(src / "ro", 0o555)
    try:
        _copy(str(src), "box:/data", recursive=True)
    finally:
        os.chmod(src / "ro", 0o755)      # so tmp_path cleanup can proceed

    dest = os.path.join(container_rootfs("box"), "data", "ro")
    assert open(os.path.join(dest, "f.txt")).read() == "DATA"
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o555
    os.chmod(dest, 0o755)


def test_copy_host_symlink_source_is_dereferenced(tmp_path, builders):
    """A host source spelled as a symlink is copied by content, as cp does.

    Termux hands out `/sdcard` and `~/storage/*` as symlinks, so this is
    the ordinary case, not an edge one. The container side already
    dereferences (resolve_container_path walks the final component too).
    """
    builders.make_container("box")
    root = container_rootfs("box")

    real_file = tmp_path / "real.txt"
    real_file.write_text("FILE")
    os.symlink(str(real_file), tmp_path / "link.txt")

    real_dir = tmp_path / "realdir"
    real_dir.mkdir()
    (real_dir / "f.txt").write_text("DIR")
    os.symlink(str(real_dir), tmp_path / "linkdir")

    _copy(str(tmp_path / "link.txt"), "box:/a.txt")
    _copy(str(tmp_path / "linkdir"), "box:/d", recursive=True)

    assert open(os.path.join(root, "a.txt")).read() == "FILE"
    assert open(os.path.join(root, "d", "f.txt")).read() == "DIR"


def test_copy_move_host_symlink_moves_the_link(tmp_path, builders):
    """--move never dereferences: mv moves the link, not its target."""
    builders.make_container("box")
    real = tmp_path / "real.txt"
    real.write_text("KEEP")
    link = tmp_path / "link.txt"
    os.symlink(str(real), link)

    _copy(str(link), "box:/moved", move=True)

    dest = os.path.join(container_rootfs("box"), "moved")
    assert os.path.islink(dest)
    assert os.readlink(dest) == str(real)
    assert not os.path.lexists(link)
    assert real.read_text() == "KEEP"


def test_copy_move_symlink_across_devices(tmp_path, builders, monkeypatch):
    """The EXDEV fallback recreates a symlink instead of failing on it."""
    import errno as _errno
    builders.make_container("box")
    real = tmp_path / "real.txt"
    real.write_text("KEEP")
    link = tmp_path / "link.txt"
    os.symlink(str(real), link)

    def no_rename(*a, **kw):
        raise OSError(_errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", no_rename)
    _copy(str(link), "box:/moved", move=True)

    dest = os.path.join(container_rootfs("box"), "moved")
    assert os.readlink(dest) == str(real)
    assert not os.path.lexists(link)
    assert real.read_text() == "KEEP"


def test_copy_into_dir_resolves_a_symlinked_target_name(tmp_path, builders):
    """`copy f box:/dir` behaves like `copy f box:/dir/f`.

    The appended base name is a path component inside the container and
    may be a symlink; joining it literally left an unresolved link at the
    leaf, which the O_NOFOLLOW open then refused.
    """
    builders.make_container("box")
    root = container_rootfs("box")
    os.makedirs(os.path.join(root, "dir"))
    os.makedirs(os.path.join(root, "real"))
    os.symlink("/real/f.txt", os.path.join(root, "dir", "f.txt"))

    src = tmp_path / "f.txt"
    src.write_text("DATA")
    _copy(str(src), "box:/dir")

    assert open(os.path.join(root, "real", "f.txt")).read() == "DATA"


def test_sync_into_dir_resolves_a_symlinked_target_name(tmp_path, builders):
    """The sync side of the same appended-name resolution."""
    builders.make_container("box")
    root = container_rootfs("box")
    os.makedirs(os.path.join(root, "dir"))
    os.makedirs(os.path.join(root, "real"))
    os.symlink("/real/f.txt", os.path.join(root, "dir", "f.txt"))

    src = tmp_path / "f.txt"
    src.write_text("DATA")
    _sync(str(src), "box:/dir")

    assert open(os.path.join(root, "real", "f.txt")).read() == "DATA"


def test_sync_dir_modes_are_idempotent(tmp_path, builders):
    """Two runs must agree: mkdir's masked mode used to differ from the chmod."""
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("x")
    os.chmod(src / "sub", 0o777)

    dest = os.path.join(container_rootfs("box"), "dest", "sub")
    _sync(str(src), "box:/dest")
    first = stat.S_IMODE(os.stat(dest).st_mode)
    _sync(str(src), "box:/dest")
    assert first == stat.S_IMODE(os.stat(dest).st_mode) == 0o777


def test_copy_recursive_skips_special_files(tmp_path, builders, capsys):
    """A FIFO is skipped with a warning instead of aborting the copy."""
    builders.make_container("box")
    src = tmp_path / "tree"
    src.mkdir()
    (src / "ok.txt").write_text("K")
    os.mkfifo(src / "pipe")

    _copy(str(src), "box:/data", recursive=True)

    root = os.path.join(container_rootfs("box"), "data")
    assert sorted(os.listdir(root)) == ["ok.txt"]
    assert "skipping special file" in capsys.readouterr().err


def test_copy_dir_without_recursive_errors(tmp_path, builders, capsys):
    builders.make_container("box")
    src = tmp_path / "tree"
    src.mkdir()
    with pytest.raises(SystemExit) as exc:
        _copy(str(src), "box:/data")
    assert exc.value.code == 1
    assert "--recursive" in capsys.readouterr().err


def test_copy_move(tmp_path, builders):
    builders.make_container("box")
    host = tmp_path / "m.txt"
    host.write_text("move me")
    _copy(str(host), "box:/root/m.txt", move=True)
    assert os.path.exists(os.path.join(container_rootfs("box"), "root", "m.txt"))
    assert not host.exists()


def test_copy_missing_source(tmp_path, builders, capsys):
    builders.make_container("box")
    with pytest.raises(SystemExit) as exc:
        _copy(str(tmp_path / "nope"), "box:/x")
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err


def test_sync_dir_into_container(tmp_path, builders):
    builders.make_container("box")
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "f1").write_text("one")
    (src / "d" / "f2").write_text("two")
    _sync(str(src), "box:/synced")
    root = container_rootfs("box")
    assert open(os.path.join(root, "synced", "f1")).read() == "one"
    assert open(os.path.join(root, "synced", "d", "f2")).read() == "two"


def test_sync_delete_removes_orphans(tmp_path, builders):
    builders.make_container("box")
    dest = os.path.join(container_rootfs("box"), "mirror")
    os.makedirs(dest)
    # Pre-existing orphan in the destination.
    with open(os.path.join(dest, "orphan"), "w") as fh:
        fh.write("old")
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep").write_text("new")

    _sync(str(src), "box:/mirror", delete=True)
    assert os.path.exists(os.path.join(dest, "keep"))
    assert not os.path.exists(os.path.join(dest, "orphan"))


def test_sync_spec_traversal_rejected(tmp_path, builders, capsys):
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    with pytest.raises(SystemExit) as exc:
        _sync(str(src), "box:../../etc")
    assert exc.value.code == 1
    assert "escapes the container directory" in capsys.readouterr().err
