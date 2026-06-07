# Integration tests for `command_copy` and `command_sync` between host paths
# and container `name:path` specs.

import os
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
