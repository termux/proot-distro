# TOCTOU containment tests for `copy` / `sync`.
#
# resolve_container_path() returns a symlink-free path, but resolving it and
# using it are two steps. A process inside the container can swap a directory
# for a symlink in between and the copy would follow it out to the host —
# measured at a 15% success rate against an unprotected copy with a single
# attacker thread.
#
# paths.pin_path() re-walks the resolved components with O_NOFOLLOW and holds
# the directory fd open, which both detects a swap (ELOOP -> abort) and pins
# what it validated (the fd names an inode, not a name). open_pinned_leaf()
# covers the final component the fd cannot.
#
# These tests drive the swap deterministically rather than racing for it.

import errno
import os
from types import SimpleNamespace

import pytest

from proot_distro import paths
from proot_distro.commands import copy as copy_mod
from proot_distro.commands import sync as sync_mod
from proot_distro.paths import container_rootfs


def _copy(source, destination, **over):
    base = dict(source=source, destination=destination, verbose=False,
                move=False, recursive=False)
    base.update(over)
    copy_mod.command_copy(SimpleNamespace(**base))


def _swap_for_symlink(path, target):
    """Replace directory *path* with a symlink to *target*."""
    os.rmdir(path)
    os.symlink(str(target), path)


# ----- pin_path mechanics -------------------------------------------------

def test_pin_path_detects_component_swap(builders, tmp_path, capsys):
    """A component that became a symlink after the resolve is refused."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    resolved = paths.resolve_container_path("box:/data/f.txt")

    _swap_for_symlink(data, tmp_path)

    with pytest.raises(SystemExit) as exc:
        with paths.pin_path("box:/data/f.txt", resolved):
            pass
    assert exc.value.code == 1
    assert "changed while it was being resolved" in capsys.readouterr().err


def test_pin_path_survives_rename_of_pinned_dir(builders, tmp_path):
    """The fd keeps naming the validated inode after the name is swapped."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    outside = tmp_path / "outside"
    outside.mkdir()
    resolved = paths.resolve_container_path("box:/data/f.txt")

    with paths.pin_path("box:/data/f.txt", resolved) as pin:
        # Attacker moves the real directory aside and drops a symlink in
        # its place *after* the pin was taken.
        os.rename(data, data + ".real")
        os.symlink(str(outside), data)
        with open(pin.io, "w") as fh:
            fh.write("PINNED")

    assert not (outside / "f.txt").exists()
    assert open(os.path.join(data + ".real", "f.txt")).read() == "PINNED"


def test_open_pinned_leaf_refuses_symlink(builders, tmp_path):
    """O_NOFOLLOW covers the one component the directory fd cannot."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    resolved = paths.resolve_container_path("box:/data/f.txt")

    with paths.pin_path("box:/data/f.txt", resolved) as pin:
        os.symlink(str(victim), os.path.join(data, "f.txt"))
        with pytest.raises(OSError) as exc:
            paths.open_pinned_leaf(pin, os.O_WRONLY | os.O_CREAT)
    assert exc.value.errno == errno.ELOOP
    assert victim.read_text() == "original"


def test_pin_path_host_spec_is_not_pinned(tmp_path):
    """Host paths are outside the threat model and stay untouched."""
    target = tmp_path / "f.txt"
    with paths.pin_path(str(target), str(target)) as pin:
        assert pin.dir_fd is None
        assert pin.io == str(target)
        assert str(pin) == str(target)


def test_pin_path_inside_yields_directory_form(builders):
    """inside=True pins the directory itself, in a form lstat() resolves."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    with paths.pin_path("box:/data", data, inside=True) as pin:
        assert pin.leaf == ""
        assert pin.io.endswith(os.sep)
        assert os.path.isdir(pin.io)
        # Not reported as a link, unlike a bare /proc/self/fd/<n>.
        assert not os.path.islink(pin.io)


def test_pin_path_falls_back_without_proc(builders, monkeypatch):
    """No /proc: degrade to the plain path instead of breaking."""
    builders.make_container("box")
    monkeypatch.setattr(paths, "_proc_fd_usable", lambda: False)
    resolved = paths.resolve_container_path("box:/etc/passwd")
    with paths.pin_path("box:/etc/passwd", resolved) as pin:
        assert pin.dir_fd is None
        assert pin.io == resolved


# ----- end to end: the swap wins the race, the command refuses ------------

def _racing_resolver(spec_prefix, on_resolve):
    """A resolve_container_path that fires on_resolve right after resolving.

    Stands in for an attacker winning the race, so the test is
    deterministic instead of hammering the window.
    """
    real = paths.resolve_container_path

    def racing(spec):
        resolved = real(spec)
        if spec.startswith(spec_prefix):
            on_resolve()
        return resolved

    return racing


def test_copy_aborts_when_component_swapped_after_resolve(
    tmp_path, builders, monkeypatch, capsys
):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = tmp_path / "payload.txt"
    payload.write_text("PWNED")

    done = []

    def attack():
        if not done:
            done.append(True)
            _swap_for_symlink(data, outside)

    monkeypatch.setattr(
        copy_mod, "resolve_container_path",
        _racing_resolver("box:", attack),
    )

    with pytest.raises(SystemExit) as exc:
        _copy(str(payload), "box:/data/f.txt")
    assert exc.value.code == 1
    assert not (outside / "f.txt").exists()
    assert "changed while it was being resolved" in capsys.readouterr().err


def test_sync_aborts_when_component_swapped_after_resolve(
    tmp_path, builders, monkeypatch, capsys
):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    outside = tmp_path / "outside"
    outside.mkdir()
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("S")

    done = []

    def attack():
        if not done:
            done.append(True)
            _swap_for_symlink(data, outside)

    monkeypatch.setattr(
        sync_mod, "resolve_container_path",
        _racing_resolver("box:", attack),
    )

    with pytest.raises(SystemExit) as exc:
        sync_mod.command_sync(SimpleNamespace(
            source=str(src), destination="box:/data", verbose=False,
            checksum=False, delete=False))
    assert exc.value.code == 1
    assert not (outside / "sub").exists()
    assert "changed while it was being resolved" in capsys.readouterr().err


def test_copy_leaf_swapped_after_resolve_is_refused(
    tmp_path, builders, monkeypatch
):
    """The final component is covered by O_NOFOLLOW, not by the fd."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    victim = tmp_path / "victim.txt"
    victim.write_text("original")
    payload = tmp_path / "payload.txt"
    payload.write_text("PWNED")

    done = []

    def attack():
        if not done:
            done.append(True)
            os.symlink(str(victim), os.path.join(data, "f.txt"))

    monkeypatch.setattr(
        copy_mod, "resolve_container_path",
        _racing_resolver("box:", attack),
    )

    with pytest.raises(SystemExit):
        _copy(str(payload), "box:/data/f.txt")
    assert victim.read_text() == "original"


# ----- the pinning must not change ordinary behaviour ---------------------

def test_copy_preserves_mode_and_mtime(tmp_path, builders):
    """_copy_file_nofollow replaces copy2; keep copy2's metadata semantics."""
    builders.make_container("box")
    src = tmp_path / "f.sh"
    src.write_text("#!/bin/sh\n")
    os.chmod(src, 0o755)
    os.utime(src, (1000000, 1000000))

    _copy(str(src), "box:/root/f.sh")

    dst = os.path.join(container_rootfs("box"), "root", "f.sh")
    st = os.stat(dst)
    assert st.st_mode & 0o777 == 0o755
    assert int(st.st_mtime) == 1000000
    assert open(dst).read() == "#!/bin/sh\n"


def test_copy_file_into_existing_directory(tmp_path, builders):
    """`copy f box:/dir` still lands at box:/dir/f, as shutil.copy2 did."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.makedirs(os.path.join(rootfs, "dir"))
    src = tmp_path / "f.txt"
    src.write_text("DATA")

    _copy(str(src), "box:/dir")

    assert open(os.path.join(rootfs, "dir", "f.txt")).read() == "DATA"
