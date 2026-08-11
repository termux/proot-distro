# Integration tests for `command_copy` and `command_sync` between host paths
# and container `name:path` specs.

import os
import stat
from types import SimpleNamespace

import pytest

from proot_distro import dirfd
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


def test_copy_move_container_symlink_moves_the_link(tmp_path, builders):
    """A move acts on the entry, container side included.

    resolve_container_path walks every component by default, so the leaf of
    a container spec was dereferenced and the move landed on the link's
    target: the real file left the container and the link stayed behind,
    dangling. mv moves the link.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    with open(os.path.join(rootfs, "target"), "w") as fh:
        fh.write("REAL")
    os.symlink("target", os.path.join(rootfs, "link"))

    dest = tmp_path / "moved"
    _copy("box:/link", str(dest), move=True)

    assert os.path.islink(dest)
    assert os.readlink(dest) == "target"
    assert not os.path.lexists(os.path.join(rootfs, "link"))
    assert open(os.path.join(rootfs, "target")).read() == "REAL"


def test_copy_move_replaces_a_symlink_at_the_destination(tmp_path, builders):
    """rename(2) replaces a link rather than writing through it, as mv does."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    with open(os.path.join(rootfs, "victim"), "w") as fh:
        fh.write("UNTOUCHED")
    os.symlink("/victim", os.path.join(rootfs, "dest"))

    payload = tmp_path / "p.txt"
    payload.write_text("NEW")
    _copy(str(payload), "box:/dest", move=True)

    landed = os.path.join(rootfs, "dest")
    assert not os.path.islink(landed)
    assert open(landed).read() == "NEW"
    assert open(os.path.join(rootfs, "victim")).read() == "UNTOUCHED"


def test_copy_plain_still_writes_through_a_symlinked_destination(tmp_path,
                                                                builders):
    """Without --move the destination link is followed, the way cp does it."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    with open(os.path.join(rootfs, "real"), "w") as fh:
        fh.write("OLD")
    os.symlink("/real", os.path.join(rootfs, "dest"))

    payload = tmp_path / "p.txt"
    payload.write_text("NEW")
    _copy(str(payload), "box:/dest")

    assert os.path.islink(os.path.join(rootfs, "dest"))
    assert open(os.path.join(rootfs, "real")).read() == "NEW"


def test_sync_host_symlink_source_is_dereferenced(tmp_path, builders):
    """`sync /sdcard box:/x` must transfer the tree, not recreate the link.

    `/sdcard` is a symlink on Termux and `copy` has always followed such a
    source; sync recreated it instead, so the same command produced a
    directory from one and a symlink from the other.
    """
    builders.make_container("box")
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "g.txt").write_text("G")
    (tree / "top.txt").write_text("T")
    link = tmp_path / "treelink"
    os.symlink(str(tree), link)

    _sync(str(link), "box:/dst")

    dst = os.path.join(container_rootfs("box"), "dst")
    assert stat.S_ISDIR(os.lstat(dst).st_mode)
    assert open(os.path.join(dst, "top.txt")).read() == "T"
    assert open(os.path.join(dst, "sub", "g.txt")).read() == "G"


def test_sync_preserves_symlinks_inside_the_tree(tmp_path, builders):
    """Only the endpoint is followed; links within the tree are copied."""
    builders.make_container("box")
    src = tmp_path / "tree"
    src.mkdir()
    (src / "f.txt").write_text("F")
    os.symlink("f.txt", src / "inner")

    _sync(str(src), "box:/dst")

    landed = os.path.join(container_rootfs("box"), "dst", "inner")
    assert os.path.islink(landed)
    assert os.readlink(landed) == "f.txt"


def test_sync_replaces_a_destination_file_with_a_directory(tmp_path, builders):
    """A file where the source has a directory used to abort the whole sync."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    dst = os.path.join(rootfs, "dst")
    os.makedirs(dst)
    with open(os.path.join(dst, "sub"), "w") as fh:
        fh.write("blocker")

    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "g.txt").write_text("G")
    (src / "top.txt").write_text("T")

    _sync(str(src), "box:/dst")

    assert stat.S_ISDIR(os.lstat(os.path.join(dst, "sub")).st_mode)
    assert open(os.path.join(dst, "sub", "g.txt")).read() == "G"
    assert open(os.path.join(dst, "top.txt")).read() == "T"


def test_sync_single_file_into_an_unwritable_dir_recovers(tmp_path, builders):
    """The documented chmod recovery has to reach the pinned endpoint too.

    make_writable() fchmod'ed the O_PATH fd pin_path hands out, which is
    EBADF, so for a single-file sync the recovery silently did nothing and
    the command failed with the permission error it was meant to clear.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    ro = os.path.join(rootfs, "ro")
    os.makedirs(ro)
    os.chmod(ro, 0o500)

    payload = tmp_path / "f.txt"
    payload.write_text("DATA")
    try:
        _sync(str(payload), "box:/ro/f.txt")
        assert open(os.path.join(ro, "f.txt")).read() == "DATA"
    finally:
        os.chmod(ro, 0o700)


def test_copy_onto_itself_is_refused(tmp_path, builders, capsys):
    """The destination is opened with O_TRUNC while the source is read."""
    builders.make_container("box")
    host = tmp_path / "f.txt"
    host.write_text("hello world")

    with pytest.raises(SystemExit) as exc:
        _copy(str(host), str(host))
    assert exc.value.code == 1
    assert "same file" in capsys.readouterr().err
    assert host.read_text() == "hello world"


def test_copy_recursive_into_a_subdirectory_of_itself_is_refused(
    tmp_path, builders, capsys
):
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("x")

    with pytest.raises(SystemExit) as exc:
        _copy(str(src), str(src / "inner"), recursive=True)
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err
    assert sorted(os.listdir(src)) == ["sub"]


def test_copy_onto_a_symlink_to_the_source_is_refused(tmp_path, builders,
                                                     capsys):
    """cp calls `cp f link` the same file when link points at f.

    The message matters, not just the exit status: comparing the two ends
    without following the destination link let this get as far as the
    O_NOFOLLOW open and fail with a bare ELOOP, which is the right outcome
    reached by accident and reported as something else entirely.
    """
    builders.make_container("box")
    target = tmp_path / "f"
    target.write_text("data")
    os.symlink("f", tmp_path / "link")

    with pytest.raises(SystemExit) as exc:
        _copy(str(target), str(tmp_path / "link"))
    assert exc.value.code == 1
    assert "are the same file" in capsys.readouterr().err
    assert target.read_text() == "data"
    assert os.path.islink(tmp_path / "link")


def test_move_onto_a_symlink_to_the_source_renames(tmp_path, builders):
    """mv does allow it: rename(2) replaces the link with the file."""
    builders.make_container("box")
    target = tmp_path / "f"
    target.write_text("data")
    link = tmp_path / "link"
    os.symlink("f", link)

    _copy(str(target), str(link), move=True)

    assert not os.path.lexists(target)
    assert not os.path.islink(link)
    assert link.read_text() == "data"


def test_move_of_a_dangling_symlink_moves_the_link(tmp_path, builders):
    """os.path.exists() follows the link and called the source missing.

    mv moves a dangling link without complaint, and after --move stopped
    dereferencing the leaf this became the ordinary case.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.symlink("/nonexistent", tmp_path / "dangling")

    _copy(str(tmp_path / "dangling"), "box:/moved", move=True)

    landed = os.path.join(rootfs, "moved")
    assert os.path.islink(landed)
    assert os.readlink(landed) == "/nonexistent"
    assert not os.path.lexists(tmp_path / "dangling")


def test_move_of_a_dangling_container_symlink_moves_the_link(tmp_path,
                                                            builders):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.symlink("/nowhere", os.path.join(rootfs, "dangling"))

    dest = tmp_path / "out"
    _copy("box:/dangling", str(dest), move=True)

    assert os.path.islink(dest)
    assert os.readlink(dest) == "/nowhere"
    assert not os.path.lexists(os.path.join(rootfs, "dangling"))


def test_sync_delete_refuses_a_source_inside_the_destination(tmp_path,
                                                            builders,
                                                            capsys):
    """`sync --delete box:/a/b box:/a` pruned box:/a/b -- its own source."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = os.path.join(rootfs, "a", "b", "sub")
    os.makedirs(src)
    with open(os.path.join(src, "f.txt"), "w") as fh:
        fh.write("keep")
    with open(os.path.join(rootfs, "a", "other"), "w") as fh:
        fh.write("sibling")

    with pytest.raises(SystemExit) as exc:
        _sync("box:/a/b", "box:/a", delete=True)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--delete" in err and "inside the destination" in err
    assert os.path.isdir(os.path.join(rootfs, "a", "b"))
    assert open(os.path.join(src, "f.txt")).read() == "keep"


def test_sync_allows_a_source_inside_the_destination_without_delete(
    tmp_path, builders
):
    """Nothing is pruned without --delete, so this is merely unusual."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = os.path.join(rootfs, "a", "b")
    os.makedirs(src)
    with open(os.path.join(src, "f.txt"), "w") as fh:
        fh.write("v")

    _sync("box:/a/b", "box:/a")

    assert os.path.isdir(src)
    assert open(os.path.join(rootfs, "a", "f.txt")).read() == "v"


def test_sync_delete_still_prunes_an_unrelated_destination(tmp_path, builders):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    dst = os.path.join(rootfs, "dst")
    os.makedirs(dst)
    for name in ("orphan", "keep"):
        with open(os.path.join(dst, name), "w") as fh:
            fh.write("k")

    src = tmp_path / "src"
    src.mkdir()
    (src / "keep").write_text("k")
    _sync(str(src), "box:/dst", delete=True)

    assert sorted(os.listdir(dst)) == ["keep"]


def test_copy_to_a_symlinked_host_parent_still_works(tmp_path, builders):
    """The /sdcard case: a symlinked parent is an ordinary destination."""
    builders.make_container("box")
    real = tmp_path / "real"
    real.mkdir()
    os.symlink(str(real), tmp_path / "sdcard")
    payload = tmp_path / "p.txt"
    payload.write_text("P")

    _copy(str(payload), str(tmp_path / "sdcard" / "out.txt"))

    assert (real / "out.txt").read_text() == "P"


def test_copy_to_a_sibling_with_a_shared_prefix_is_not_a_fold(tmp_path,
                                                              builders):
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("x")

    _copy(str(src), str(tmp_path / "tree2"), recursive=True)

    assert (tmp_path / "tree2" / "sub" / "f.txt").read_text() == "x"


def test_copy_writes_through_a_symlinked_host_destination(tmp_path, builders):
    """cp writes what a destination link points at; a host end now does too.

    The container end already did (the chroot walk resolves the last
    component), so the two disagreed: the same copy succeeded for `box:/link`
    and died with a bare ELOOP for a host path.
    """
    builders.make_container("box")
    real = tmp_path / "real"
    real.write_text("OLD")
    link = tmp_path / "link"
    os.symlink(str(real), link)
    payload = tmp_path / "p.txt"
    payload.write_text("NEW")

    _copy(str(payload), str(link))

    assert os.path.islink(link)
    assert real.read_text() == "NEW"


def test_move_into_a_symlinked_container_directory_keeps_the_link(tmp_path,
                                                                 builders):
    """mv moves into the directory a link names and leaves the link alone.

    The test used os.path.isdir() on the unresolved leaf, which asks the
    *host* whether the guest's link target is a directory. With a target only
    the container has, the answer was no and the move replaced the link — so
    an ordinary release symlink became a regular file and its directory was
    emptied.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.makedirs(os.path.join(rootfs, "opt", "releases", "v1"))
    os.symlink("/opt/releases/v1", os.path.join(rootfs, "opt", "current"))
    with open(os.path.join(rootfs, "payload"), "w") as fh:
        fh.write("P")

    _copy("box:/payload", "box:/opt/current", move=True)

    assert os.readlink(os.path.join(rootfs, "opt", "current")) == \
        "/opt/releases/v1"
    assert open(os.path.join(rootfs, "opt", "releases", "v1",
                             "payload")).read() == "P"


def test_move_replaces_a_link_dangling_inside_the_container(tmp_path,
                                                           builders):
    """The mirror image: a target the host has and the container does not.

    `/dir -> /tmp` is dangling seen from inside, so mv replaces it — but the
    host has a /tmp, so isdir() said yes and the move invented <rootfs>/tmp
    and landed the file in there instead.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.symlink("/tmp", os.path.join(rootfs, "dir"))
    with open(os.path.join(rootfs, "payload"), "w") as fh:
        fh.write("P")

    _copy("box:/payload", "box:/dir", move=True)

    assert not os.path.islink(os.path.join(rootfs, "dir"))
    assert open(os.path.join(rootfs, "dir")).read() == "P"
    assert not os.path.exists(os.path.join(rootfs, "tmp"))


def test_copy_replaces_an_existing_destination_file(tmp_path, builders):
    """The write goes via a temp file now; the result must not change."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    with open(os.path.join(rootfs, "dest"), "w") as fh:
        fh.write("OLD CONTENT, LONGER")
    payload = tmp_path / "p.txt"
    payload.write_text("NEW")
    os.chmod(payload, 0o640)

    _copy(str(payload), "box:/dest")

    assert open(os.path.join(rootfs, "dest")).read() == "NEW"
    assert stat.S_IMODE(os.stat(os.path.join(rootfs, "dest")).st_mode) == 0o640
    assert not [n for n in os.listdir(rootfs) if n.endswith(dirfd.TMP_SUFFIX)]


def test_sync_reports_a_source_that_stops_being_a_symlink(tmp_path, builders,
                                                          capsys,
                                                          monkeypatch):
    """A source-side race is a skipped entry, not a traceback.

    The source lock is shared, so a guest may be running while sync reads.
    An entry listed as a symlink and swapped for a regular file makes
    readlink(2) fail with EINVAL, which used to escape command_sync entirely.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "entry").write_text("plain file")
    (src / "other").write_text("survives")

    real_lstat = dirfd.lstat_at

    class _Lying:
        """Reports a regular file as a symlink, as a mid-walk swap would."""

        def __init__(self, st):
            self._st = st
            self.st_mode = stat.S_IFLNK | 0o777

        def __getattr__(self, name):
            return getattr(self._st, name)

    def lying_lstat(dir_fd, name):
        st = real_lstat(dir_fd, name)
        if name == "entry" and stat.S_ISREG(st.st_mode):
            return _Lying(st)
        return st

    monkeypatch.setattr(dirfd, "lstat_at", lying_lstat)
    with pytest.raises(SystemExit) as exc:
        _sync(str(src), "box:/dst")

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "cannot copy symlink" in err and "entry" in err
    dst = os.path.join(container_rootfs("box"), "dst")
    assert sorted(os.listdir(dst)) == ["other"]


def test_sync_refuses_to_replace_a_directory_with_a_file(tmp_path, builders,
                                                         capsys):
    """rsync refuses this too, and one entry must not abandon the rest.

    The rename over the directory failed with EISDIR and exited, naming the
    temp file rather than the reason.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "f").write_text("plain")
    (src / "ok.txt").write_text("fine")

    rootfs = container_rootfs("box")
    dst = os.path.join(rootfs, "dst")
    os.makedirs(os.path.join(dst, "f"))
    with open(os.path.join(dst, "f", "keep"), "w") as fh:
        fh.write("kept")

    # Skipped, not fatal: ok.txt is still transferred. The status reports
    # the transfer incomplete, which is what rsync does for the same.
    with pytest.raises(SystemExit) as exc:
        _sync(str(src), "box:/dst")

    assert exc.value.code == 1
    assert "cannot replace directory" in capsys.readouterr().err
    assert open(os.path.join(dst, "f", "keep")).read() == "kept"
    assert open(os.path.join(dst, "ok.txt")).read() == "fine"


def test_sync_delete_leaves_a_directory_it_could_not_replace(tmp_path,
                                                             builders):
    """A skipped entry must take its destination out of --delete's reach.

    The name is in the source, so the prune pass counted the directory
    standing in its place as "present" and walked into it — and nothing
    inside had a counterpart in a source that holds a plain file there, so
    every one of its children was deleted. The directory the mirror pass
    deliberately refused to touch came out empty.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "f").write_text("plain")

    rootfs = container_rootfs("box")
    dst = os.path.join(rootfs, "dst")
    os.makedirs(os.path.join(dst, "f"))
    with open(os.path.join(dst, "f", "keep"), "w") as fh:
        fh.write("kept")

    with pytest.raises(SystemExit):
        _sync(str(src), "box:/dst", delete=True)

    assert open(os.path.join(dst, "f", "keep")).read() == "kept"


def test_sync_delete_leaves_the_destination_of_a_source_special_file(
    tmp_path, builders
):
    """A FIFO is never mirrored, so its destination is not ours to prune."""
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    os.mkfifo(str(src / "pipe"))

    rootfs = container_rootfs("box")
    dst = os.path.join(rootfs, "dst")
    os.makedirs(os.path.join(dst, "pipe"))
    with open(os.path.join(dst, "pipe", "keep"), "w") as fh:
        fh.write("kept")

    _sync(str(src), "box:/dst", delete=True)

    assert open(os.path.join(dst, "pipe", "keep")).read() == "kept"


def test_sync_handles_a_name_too_long_for_the_temp_suffix(tmp_path, builders):
    """`.~pd_sync` must not push a legal name past NAME_MAX.

    The temp file inherited the entry's name plus nine bytes, so an entry
    already near the 255-byte limit failed with ENAMETOOLONG — and that
    failure ended the command, so every later entry went untransferred too.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    long_name = "n" * 250
    (src / long_name).write_text("data")
    (src / "zzz.txt").write_text("after")

    _sync(str(src), "box:/dst")

    dst = os.path.join(container_rootfs("box"), "dst")
    assert open(os.path.join(dst, long_name)).read() == "data"
    assert open(os.path.join(dst, "zzz.txt")).read() == "after"


def test_copy_handles_a_name_too_long_for_the_temp_suffix(tmp_path, builders):
    """`.~pd_copy` had the same nine bytes to spare, and the same problem."""
    builders.make_container("box")
    long_name = "m" * 250
    src = tmp_path / long_name
    src.write_text("data")

    _copy(str(src), "box:/" + long_name)

    assert open(os.path.join(container_rootfs("box"), long_name)).read() == \
        "data"


def test_sync_steps_over_an_entry_it_cannot_write(tmp_path, builders, capsys):
    """One unwritable entry must not abandon the rest of the transfer.

    A container can arrange this at will: a *directory* under the temp
    name is not a leftover to be unlinked, it is EISDIR, and the write
    used to end the command on the spot. It is now reported, counted and
    stepped over — and the exit status still says the transfer was
    incomplete, as it did when the first such entry was fatal.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    (src / "b.txt").write_text("b")

    rootfs = container_rootfs("box")
    dst = os.path.join(rootfs, "dst")
    os.makedirs(dst)
    os.mkdir(os.path.join(dst, "a.txt" + dirfd.TMP_SUFFIX.replace("copy",
                                                                  "sync")))

    with pytest.raises(SystemExit) as exc:
        _sync(str(src), "box:/dst")

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "cannot write to" in err.lower()
    # The entry after the one in the way was still transferred.
    assert open(os.path.join(dst, "b.txt")).read() == "b"


def test_sync_leaves_the_old_content_when_a_write_is_blocked(tmp_path,
                                                             builders):
    """A blocked write must not take the destination down with it.

    The content goes to a temp file and is renamed into place, so a write
    that never gets that far leaves what was already there — and the rest
    of the tree is still transferred around it.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("brand new content")
    (src / "b.txt").write_text("b")

    rootfs = container_rootfs("box")
    dst = os.path.join(rootfs, "dst")
    os.makedirs(dst)
    with open(os.path.join(dst, "a.txt"), "w") as fh:
        fh.write("old")
    os.mkdir(os.path.join(dst, "a.txt.~pd_sync"))

    with pytest.raises(SystemExit) as exc:
        _sync(str(src), "box:/dst", delete=True)

    assert exc.value.code == 1
    assert open(os.path.join(dst, "a.txt")).read() == "old"
    assert open(os.path.join(dst, "b.txt")).read() == "b"


def test_copy_recursive_into_an_existing_directory_lands_inside_it(tmp_path,
                                                                   builders):
    """`cp -r src destdir` puts src *in* destdir; so does `--move`.

    Only a file destination was appended to, so a recursive directory copy
    onto a directory that already existed died on the mkdir's EEXIST.
    """
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "b.txt").write_text("b")

    rootfs = container_rootfs("box")
    os.makedirs(os.path.join(rootfs, "dst"))

    _copy(str(src), "box:/dst", recursive=True)

    assert open(os.path.join(rootfs, "dst", "tree", "sub",
                             "b.txt")).read() == "b"


def test_copy_recursive_to_a_new_destination_still_becomes_it(tmp_path,
                                                              builders):
    """A destination that does not exist is created *as* the source."""
    builders.make_container("box")
    src = tmp_path / "tree"
    src.mkdir()
    (src / "a.txt").write_text("a")

    _copy(str(src), "box:/dst", recursive=True)

    assert open(os.path.join(container_rootfs("box"), "dst",
                             "a.txt")).read() == "a"
