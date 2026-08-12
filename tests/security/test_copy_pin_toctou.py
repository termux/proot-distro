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

from proot_distro import dirfd, paths
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
        fd = dirfd.open_file_at(pin.dir_fd, pin.leaf,
                                os.O_WRONLY | os.O_CREAT)
        try:
            os.write(fd, b"PINNED")
        finally:
            os.close(fd)

    assert not (outside / "f.txt").exists()
    assert open(os.path.join(data + ".real", "f.txt")).read() == "PINNED"


def test_open_file_at_refuses_symlink(builders, tmp_path):
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
            dirfd.open_file_at(pin.dir_fd, pin.leaf, os.O_WRONLY | os.O_CREAT)
    assert exc.value.errno == errno.ELOOP
    assert victim.read_text() == "original"


def test_pin_path_host_spec_still_yields_a_dir_fd(tmp_path):
    """Host paths are not walked, but callers get the same (fd, leaf) pair."""
    target = tmp_path / "f.txt"
    with paths.pin_path(str(target), str(target)) as pin:
        assert pin.leaf == "f.txt"
        assert os.path.samestat(os.fstat(pin.dir_fd), os.stat(tmp_path))
        assert str(pin) == str(target)


def test_pin_path_inside_pins_the_directory_itself(builders):
    """inside=True walks the last component too, leaving an empty leaf."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    with paths.pin_path("box:/data", data, inside=True) as pin:
        assert pin.leaf == ""
        assert os.path.samestat(os.fstat(pin.dir_fd), os.stat(data))


def test_pin_path_inside_refuses_symlinked_root(builders, tmp_path, capsys):
    """A root that became a symlink is refused, not written through."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    resolved = paths.resolve_container_path("box:/data")
    _swap_for_symlink(data, tmp_path)

    with pytest.raises(SystemExit):
        with paths.pin_path("box:/data", resolved, inside=True):
            pass
    assert "changed while it was being resolved" in capsys.readouterr().err


# ----- end to end: the swap wins the race, the command refuses ------------

def _racing_resolver(spec_prefix, on_resolve):
    """A resolve_container_path that fires on_resolve right after resolving.

    Stands in for an attacker winning the race, so the test is
    deterministic instead of hammering the window.
    """
    real = paths.resolve_container_path

    def racing(spec, **kwargs):
        resolved = real(spec, **kwargs)
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


# ----- the destination's missing parents ----------------------------------
#
# Creating them with os.makedirs() before pinning put a path-addressed
# write ahead of the guarantee: the swap was still detected and the copy
# still refused, but only after makedirs() had followed the planted link
# and built the tree outside the container. pin_path(create=True) makes
# them along the O_NOFOLLOW walk instead, so there is no window at all.

def test_copy_makes_no_directories_outside_before_the_pin(
    tmp_path, builders, monkeypatch, capsys
):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = tmp_path / "payload.txt"
    payload.write_text("PWNED")

    # 'stage' does not exist at resolve time, so it is taken literally;
    # the attacker plants it as a link before the parents get made.
    done = []

    def attack():
        if not done:
            done.append(True)
            os.symlink(str(outside), os.path.join(rootfs, "stage"))

    monkeypatch.setattr(copy_mod, "resolve_container_path",
                        _racing_resolver("box:", attack))

    with pytest.raises(SystemExit) as exc:
        _copy(str(payload), "box:/stage/deep/nested/f.txt")

    assert exc.value.code == 1
    assert done, "the swap never fired; test is not exercising the race"
    assert sorted(os.listdir(outside)) == []
    assert "changed while it was being resolved" in capsys.readouterr().err


def test_sync_makes_no_directories_outside_before_the_pin(
    tmp_path, builders, monkeypatch, capsys
):
    """sync creates the destination root itself, so it had the same hole."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("S")

    done = []

    def attack():
        if not done:
            done.append(True)
            os.symlink(str(outside), os.path.join(rootfs, "stage"))

    monkeypatch.setattr(sync_mod, "resolve_container_path",
                        _racing_resolver("box:", attack))

    with pytest.raises(SystemExit):
        sync_mod.command_sync(SimpleNamespace(
            source=str(src), destination="box:/stage/deep/dest",
            verbose=False, checksum=False, delete=False))

    assert done
    assert sorted(os.listdir(outside)) == []
    assert "changed while it was being resolved" in capsys.readouterr().err


def test_copy_creates_missing_parents_inside_the_container(tmp_path, builders):
    """The create=True walk must still do what makedirs() did."""
    builders.make_container("box")
    payload = tmp_path / "p.txt"
    payload.write_text("DATA")

    _copy(str(payload), "box:/a/b/c/p.txt")

    landed = os.path.join(container_rootfs("box"), "a", "b", "c", "p.txt")
    assert open(landed).read() == "DATA"


# ----- below the pinned root: the dir_fd walk -----------------------------
#
# Pinning the endpoints leaves everything the walk creates underneath them
# addressed by name. Measured against that version: a component swapped at
# depth diverted 8 files out of a `sync` and 7 out of a recursive `copy`.
# The walk now carries a directory fd per level instead.

def test_copy_tree_at_ignores_a_swap_below_the_root(tmp_path):
    """A directory swapped mid-walk cannot redirect the rest of the copy."""
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    for i in range(4):
        (src / "sub" / f"f{i}.txt").write_text(str(i))
    dst = tmp_path / "dst"
    dst.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()

    swapped = []

    def on_entry(rel):
        # Once the first file of sub/ is in, move the real directory
        # aside and leave a symlink to the host in its place.
        if rel.startswith("sub/") and not swapped:
            swapped.append(rel)
            os.rename(dst / "sub", dst / "sub.real")
            os.symlink(str(outside), dst / "sub")

    sfd = dirfd.opendir(str(src))
    dfd = dirfd.opendir(str(dst))
    try:
        dirfd.copy_tree_at(sfd, dfd, on_entry=on_entry)
    finally:
        os.close(sfd)
        os.close(dfd)

    assert swapped, "the swap never fired; test is not exercising the race"
    assert sorted(os.listdir(outside)) == []
    # Everything landed in the directory the fd was pinned to.
    assert len(os.listdir(dst / "sub.real")) == 4


def test_copy_recursive_swap_below_root_does_not_escape(
    tmp_path, builders, monkeypatch
):
    """Command level: swap a destination subdirectory mid-transfer."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    for i in range(4):
        (src / "sub" / f"f{i}.txt").write_text(str(i))

    real_copy = dirfd.copy_file_at
    fired = []

    def racing(src_dir_fd, src_name, dst_dir_fd, dst_name, src_st=None,
               **kwargs):
        real_copy(src_dir_fd, src_name, dst_dir_fd, dst_name, src_st,
                  **kwargs)
        target = os.path.join(rootfs, "dest", "sub")
        if not fired and os.path.isdir(target):
            fired.append(True)
            os.rename(target, target + ".real")
            os.symlink(str(outside), target)

    monkeypatch.setattr(dirfd, "copy_file_at", racing)

    try:
        _copy(str(src), "box:/dest", recursive=True)
    except SystemExit:
        pass

    assert fired
    assert sorted(os.listdir(outside)) == []


def test_sync_swap_below_root_does_not_escape(
    tmp_path, builders, monkeypatch, capsys
):
    """Command level: a synced subdirectory turns into a symlink."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = os.path.join(rootfs, "data")
    os.makedirs(os.path.join(dest, "sub"))
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    for i in range(4):
        (src / "sub" / f"f{i}.txt").write_text(str(i))

    real_sync_dir = sync_mod._sync_dir
    fired = []

    def racing(dst_fd, name):
        created = real_sync_dir(dst_fd, name)
        target = os.path.join(dest, "sub")
        if not fired and name == "sub" and os.path.isdir(target):
            fired.append(True)
            os.rename(target, target + ".real")
            os.symlink(str(outside), target)
        return created

    monkeypatch.setattr(sync_mod, "_sync_dir", racing)

    # A subtree that could not be descended into is a skipped entry, so
    # the command reports the transfer incomplete rather than success.
    with pytest.raises(SystemExit) as exc:
        sync_mod.command_sync(SimpleNamespace(
            source=str(src), destination="box:/data", verbose=False,
            checksum=False, delete=False))

    assert exc.value.code == 1
    assert fired
    assert sorted(os.listdir(outside)) == []
    assert "changed to a symlink during the transfer" in capsys.readouterr().err


def test_sync_delete_does_not_walk_out_through_a_symlink(tmp_path, builders):
    """--delete must not enumerate (and remove) entries outside the rootfs."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("KEEP")

    dest = os.path.join(rootfs, "data")
    os.makedirs(dest)
    # An extra entry with no counterpart in the source, pointing at the host.
    os.symlink(str(outside), os.path.join(dest, "extra"))

    src = tmp_path / "tree"
    src.mkdir()
    (src / "keep.txt").write_text("K")

    sync_mod.command_sync(SimpleNamespace(
        source=str(src), destination="box:/data", verbose=False,
        checksum=False, delete=True))

    # The link itself is removed; what it pointed at is untouched.
    assert not os.path.lexists(os.path.join(dest, "extra"))
    assert (outside / "keep.txt").read_text() == "KEEP"


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
