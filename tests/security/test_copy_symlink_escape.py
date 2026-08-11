# Containment tests for `copy` / `sync` against symlinks planted inside a
# container rootfs.
#
# A symlink like `escape -> /` is perfectly ordinary seen from inside a
# container, but on the host it points at the host root. Resolving a
# `name:path` spec lexically would follow it, so a `copy` into the container
# could write anywhere on the host filesystem (and a `copy` out of it could
# read any host file). Every path below must stay inside the rootfs.

import os
import stat
from types import SimpleNamespace

import pytest

from proot_distro import dirfd
from proot_distro.commands.copy import command_copy
from proot_distro.commands.sync import command_sync
from proot_distro.paths import container_rootfs, resolve_container_path


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


def _inside(path, rootfs):
    return os.path.abspath(path).startswith(os.path.abspath(rootfs) + os.sep)


# ----- copy: writing through a planted symlink ----------------------------

def test_copy_absolute_symlink_dest_stays_inside(tmp_path, builders):
    """`escape -> <host dir>` must not receive the copied file."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), os.path.join(rootfs, "escape"))

    payload = tmp_path / "payload.txt"
    payload.write_text("PWNED")
    _copy(str(payload), "box:/escape/owned.txt")

    assert not (outside / "owned.txt").exists()
    # The link target is re-anchored at the rootfs, as the guest sees it.
    landed = os.path.join(rootfs, str(outside).lstrip("/"), "owned.txt")
    assert open(landed).read() == "PWNED"


def test_copy_relative_symlink_dest_is_clamped(tmp_path, builders):
    """A `../../..`-style link target cannot climb above the rootfs."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.symlink("../" * 12 + "tmp", os.path.join(rootfs, "up"))

    payload = tmp_path / "p.txt"
    payload.write_text("X")
    _copy(str(payload), "box:/up/marker.txt")

    assert not os.path.exists("/tmp/marker.txt")
    assert open(os.path.join(rootfs, "tmp", "marker.txt")).read() == "X"


def test_copy_symlinked_parent_component(tmp_path, builders):
    """The escape may sit anywhere in the path, not just at the front."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    (outside / "deep").mkdir(parents=True)
    os.makedirs(os.path.join(rootfs, "var"))
    os.symlink(str(outside), os.path.join(rootfs, "var", "spool"))

    payload = tmp_path / "p.txt"
    payload.write_text("X")
    _copy(str(payload), "box:/var/spool/deep/f.txt")

    assert not (outside / "deep" / "f.txt").exists()


def test_copy_recursive_into_symlinked_dir(tmp_path, builders):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), os.path.join(rootfs, "escape"))

    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "a.txt").write_text("a")
    _copy(str(src), "box:/escape/tree", recursive=True)

    assert not (outside / "tree").exists()


def test_move_through_symlink_stays_inside(tmp_path, builders):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), os.path.join(rootfs, "escape"))

    payload = tmp_path / "m.txt"
    payload.write_text("M")
    _copy(str(payload), "box:/escape/moved.txt", move=True)

    assert not (outside / "moved.txt").exists()


def test_copy_into_dir_reanchors_the_appended_name(tmp_path, builders):
    """`copy f box:/dir` resolves box:/dir/f, and stays inside doing it.

    The appended base name goes through the same chroot walk as one
    written in the spec, so a link planted at that name is re-anchored at
    the rootfs rather than followed out to the host.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.makedirs(os.path.join(rootfs, "dir"))
    os.symlink(os.path.join(str(outside), "f.txt"),
               os.path.join(rootfs, "dir", "f.txt"))

    payload = tmp_path / "f.txt"
    payload.write_text("PWNED")
    _copy(str(payload), "box:/dir")

    assert not (outside / "f.txt").exists()
    landed = os.path.join(rootfs, str(outside).lstrip("/"), "f.txt")
    assert open(landed).read() == "PWNED"


def test_sync_into_dir_reanchors_the_appended_name(tmp_path, builders):
    """sync re-anchors it too, and refuses rather than reaching the host.

    Unlike copy, sync does not create a destination parent for a single
    file (rsync does not either), so re-anchoring a link that points at a
    host directory with no counterpart inside the rootfs leaves nothing
    to write into and the command stops. Either way nothing lands outside.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.makedirs(os.path.join(rootfs, "dir"))
    os.symlink(os.path.join(str(outside), "f.txt"),
               os.path.join(rootfs, "dir", "f.txt"))

    payload = tmp_path / "f.txt"
    payload.write_text("PWNED")
    with pytest.raises(SystemExit) as exc:
        _sync(str(payload), "box:/dir")

    assert exc.value.code == 1
    assert not (outside / "f.txt").exists()


# ----- copy: reading through a planted symlink ----------------------------

def test_copy_source_symlink_cannot_read_host(tmp_path, builders):
    """`box:/leak/secret.txt` must not resolve to the host's file."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    os.symlink(str(tmp_path), os.path.join(rootfs, "leak"))

    out = tmp_path / "stolen.txt"
    with pytest.raises(SystemExit) as exc:
        _copy("box:/leak/secret.txt", str(out))
    assert exc.value.code == 1
    assert not out.exists()


def test_copy_source_symlink_reads_container_copy(tmp_path, builders):
    """Re-anchored at the rootfs, the link resolves to container content."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.symlink("/etc/passwd", os.path.join(rootfs, "pw"))

    out = tmp_path / "out.txt"
    _copy("box:/pw", str(out))
    assert out.read_text() == open(os.path.join(rootfs, "etc", "passwd")).read()


# ----- sync ---------------------------------------------------------------

def test_sync_dest_root_symlink_stays_inside(tmp_path, builders):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), os.path.join(rootfs, "esc"))

    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("S")
    _sync(str(src), "box:/esc")

    assert not (outside / "sub").exists()


def test_sync_replaces_symlinked_subdir_in_dest(tmp_path, builders):
    """A symlink already sitting in the destination tree is not descended."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    dest = os.path.join(rootfs, "data")
    os.makedirs(dest)
    os.symlink(str(outside), os.path.join(dest, "sub"))

    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("S")
    _sync(str(src), "box:/data")

    assert not (outside / "f.txt").exists()
    assert not os.path.islink(os.path.join(dest, "sub"))
    assert open(os.path.join(dest, "sub", "f.txt")).read() == "S"


def test_sync_source_symlink_tree_not_followed_out(tmp_path, builders):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    (tmp_path / "secret.txt").write_text("TOPSECRET")
    os.makedirs(os.path.join(rootfs, "d"))
    os.symlink(str(tmp_path), os.path.join(rootfs, "d", "leak"))

    dest = tmp_path / "dump"
    _sync("box:/d", str(dest))

    # The link is mirrored as a link, never walked into: its contents are
    # not pulled out of the container along with it.
    assert os.path.islink(dest / "leak")
    assert not (dest / "secret.txt").exists()
    assert sorted(os.listdir(dest)) == ["leak"]


def test_sync_delete_does_not_chmod_host_file_through_symlink(
    tmp_path, builders
):
    """`--delete`'s chmod fallback must not act on symlink targets.

    shutil.rmtree() failing with EPERM sends `sync` through a walk that
    chmods every entry to force the removal through. os.chmod() follows
    symlinks, so a link inside the removed subtree used to hand the
    container a mode change on any host file.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")

    victim = tmp_path / "victim"
    victim.write_text("secret")
    os.chmod(victim, 0o400)

    # `extra` has no counterpart in the source, so --delete removes it;
    # mode 0500 makes rmtree's first attempt fail with PermissionError.
    dest = os.path.join(rootfs, "data")
    extra = os.path.join(dest, "extra")
    os.makedirs(extra)
    os.symlink(str(victim), os.path.join(extra, "link"))
    os.chmod(extra, 0o500)

    src = tmp_path / "tree"
    src.mkdir()
    (src / "keep.txt").write_text("k")
    _sync(str(src), "box:/data", delete=True)

    assert stat.S_IMODE(os.stat(victim).st_mode) == 0o400
    assert victim.read_text() == "secret"
    # The fallback still does its job: the unwritable subtree is gone.
    assert not os.path.exists(extra)
    assert os.path.exists(os.path.join(dest, "keep.txt"))


def test_rmtree_force_does_not_chmod_symlink_targets(tmp_path):
    """The force path chmods directories it owns, never a link's target."""
    victim = tmp_path / "victim"
    victim.write_text("x")
    os.chmod(victim, 0o400)

    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    os.symlink(str(victim), tree / "sub" / "link")
    os.chmod(tree / "sub", 0o500)

    fd = dirfd.opendir(str(tmp_path))
    try:
        dirfd.rmtree_at(fd, "tree", force=True)
    finally:
        os.close(fd)

    assert not tree.exists()
    assert stat.S_IMODE(os.stat(victim).st_mode) == 0o400
    assert victim.read_text() == "x"


# ----- resolver-level guarantees ------------------------------------------

@pytest.mark.parametrize("target", ["/", "/..", "../../../..", "/etc/../.."])
def test_resolver_never_leaves_rootfs(builders, target):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.symlink(target, os.path.join(rootfs, "link"))
    resolved = resolve_container_path("box:/link/x")
    assert _inside(resolved, rootfs)


def test_resolver_rejects_symlink_loop(builders, capsys):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.symlink("b", os.path.join(rootfs, "a"))
    os.symlink("a", os.path.join(rootfs, "b"))
    with pytest.raises(SystemExit) as exc:
        resolve_container_path("box:/a")
    assert exc.value.code == 1
    assert "too many symbolic links" in capsys.readouterr().err
