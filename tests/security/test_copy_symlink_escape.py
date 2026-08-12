# Containment tests for `copy` / `sync` against symlinks planted inside a
# container rootfs.
#
# A symlink like `escape -> /` is perfectly ordinary seen from inside a
# container, but on the host it points at the host root. Resolving a
# `name:path` spec lexically would follow it, so a `copy` into the container
# could write anywhere on the host filesystem (and a `copy` out of it could
# read any host file). Every path below must stay inside the rootfs.

import contextlib
import errno
import os
import signal
import stat
from types import SimpleNamespace

import pytest

from proot_distro import dirfd
from proot_distro.commands.copy import command_copy
from proot_distro.commands.sync import command_sync
from proot_distro.paths import container_rootfs, resolve_container_path


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

    A removal that fails with EPERM sends `sync` through a walk that chmods
    entries to force it through. os.chmod() follows symlinks, so a link
    inside the removed subtree used to hand the container a mode change on
    any host file.

    `extra` is mode 0000, not 0500: an unwritable directory is still
    *readable*, so the descent succeeds and the fallback never runs at all.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")

    victim = tmp_path / "victim"
    victim.write_text("secret")
    os.chmod(victim, 0o400)

    # `extra` has no counterpart in the source, so --delete removes it;
    # mode 0000 makes the first attempt fail with PermissionError.
    dest = os.path.join(rootfs, "data")
    extra = os.path.join(dest, "extra")
    os.makedirs(extra)
    os.symlink(str(victim), os.path.join(extra, "link"))
    os.chmod(extra, 0o000)

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
    os.chmod(tree / "sub", 0o000)

    fd = dirfd.opendir(str(tmp_path))
    try:
        dirfd.rmtree_at(fd, "tree", force=True)
    finally:
        os.close(fd)

    assert not tree.exists()
    assert stat.S_IMODE(os.stat(victim).st_mode) == 0o400
    assert victim.read_text() == "x"


def test_rmtree_force_refuses_a_directory_swapped_for_a_symlink(
    tmp_path, monkeypatch
):
    """The force path must not chmod an entry replaced mid-retry.

    rmtree_at lstats an entry as a directory, fails to open it, then makes
    it readable and tries again. Naming the entry in that chmod handed the
    container a mode change on any host file it could point a link at, with
    bits it chose itself by picking the mode of the directory it planted:
    0044 | 0700 is 0746, so a 0600 private file came out group- and
    world-writable. Racing it for real took a few thousand attempts, so the
    swap is forced here to keep the test deterministic.
    """
    victim = tmp_path / "victim"
    victim.write_text("private key")
    os.chmod(victim, 0o600)

    tree = tmp_path / "tree"
    bait = tree / "bait"
    bait.mkdir(parents=True)
    os.chmod(bait, 0o044)          # unreadable: the descent gives EACCES

    real_opendir_at = dirfd.opendir_at
    swapped = []

    def swapping_opendir_at(dir_fd, name):
        if name == "bait" and not swapped:
            swapped.append(name)
            os.rmdir(bait)         # the guest wins the window
            os.symlink(str(victim), str(bait))
            raise PermissionError(errno.EACCES, "Permission denied", name)
        return real_opendir_at(dir_fd, name)

    monkeypatch.setattr(dirfd, "opendir_at", swapping_opendir_at)

    fd = dirfd.opendir(str(tree))
    try:
        with pytest.raises(OSError):
            dirfd.rmtree_at(fd, "bait", force=True)
    finally:
        os.close(fd)

    assert swapped, "the swap never happened; the test proves nothing"
    assert stat.S_IMODE(os.stat(victim).st_mode) == 0o600
    assert victim.read_text() == "private key"


# ----- hostile file types planted at an endpoint --------------------------

def test_copy_refuses_a_fifo_planted_at_the_destination(tmp_path, builders,
                                                        capsys):
    """O_NOFOLLOW refuses a symlink but says nothing about a pipe.

    Opening one for writing waits for a reader the guest need never supply,
    which hung the copy indefinitely — including when the pipe was reached
    through a symlink the resolver had already followed, as here.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.mkfifo(os.path.join(rootfs, "realfifo"))
    os.symlink("/realfifo", os.path.join(rootfs, "innocent"))

    payload = tmp_path / "p.txt"
    payload.write_text("X")

    with _deadline():
        with pytest.raises(SystemExit) as exc:
            _copy(str(payload), "box:/innocent")
    assert exc.value.code == 1
    # Still a pipe: nothing was written through it, nothing replaced it, and
    # the resolver did follow the link to get here.
    assert stat.S_ISFIFO(os.lstat(os.path.join(rootfs, "realfifo")).st_mode)
    assert "realfifo" in capsys.readouterr().err


def test_copy_refuses_a_fifo_named_as_the_source(tmp_path, builders, capsys):
    builders.make_container("box")
    os.mkfifo(os.path.join(container_rootfs("box"), "srcfifo"))

    with _deadline():
        with pytest.raises(SystemExit) as exc:
            _copy("box:/srcfifo", str(tmp_path / "out"))
    assert exc.value.code == 1
    assert "not a regular file or directory" in capsys.readouterr().err
    assert not (tmp_path / "out").exists()


def test_sync_survives_a_fifo_planted_at_the_destination(tmp_path, builders):
    """sync writes a temp file and renames, so a pipe is simply replaced."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.mkfifo(os.path.join(rootfs, "fifo"))

    payload = tmp_path / "p.txt"
    payload.write_text("X")

    with _deadline():
        _sync(str(payload), "box:/fifo")
    landed = os.path.join(rootfs, "fifo")
    assert stat.S_ISREG(os.lstat(landed).st_mode)
    assert open(landed).read() == "X"


# ----- a planted link that folds the destination into the source ----------

def test_copy_refuses_a_destination_a_link_folds_into_the_source(
    tmp_path, builders, capsys
):
    """`backup -> /data` makes `copy -r box:/data box:/backup` self-copying.

    The overlap is invisible in the specs and only shows up once both sides
    are resolved. Left unchecked the walk recursed until the interpreter's
    stack gave out, dumping a traceback and leaving a thousand directories
    behind.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(os.path.join(data, "sub"))
    with open(os.path.join(data, "sub", "f.txt"), "w") as fh:
        fh.write("x")
    os.symlink("/data", os.path.join(rootfs, "backup"))

    with pytest.raises(SystemExit) as exc:
        _copy("box:/data", "box:/backup/inner", recursive=True)
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err
    assert sorted(os.listdir(data)) == ["sub"]


def test_sync_refuses_a_destination_a_link_folds_into_the_source(
    tmp_path, builders, capsys
):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(data)
    with open(os.path.join(data, "f.txt"), "w") as fh:
        fh.write("x")
    os.symlink("/data", os.path.join(rootfs, "mirror"))

    with pytest.raises(SystemExit) as exc:
        _sync("box:/data", "box:/mirror/inner")
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err
    assert sorted(os.listdir(data)) == ["f.txt"]


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


def test_copy_refuses_a_host_link_folding_the_destination_into_the_source(
    tmp_path, builders, capsys
):
    """Only container paths arrive symlink-free; host ones are not walked.

    So the fold this guard exists for was still reachable through a host
    link, and still recursed to the interpreter's limit leaving hundreds of
    stray directories behind.
    """
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("x")
    os.symlink(str(src), tmp_path / "link")

    with pytest.raises(SystemExit) as exc:
        _copy(str(src), str(tmp_path / "link" / "inner"), recursive=True)
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err
    assert sorted(os.listdir(src)) == ["sub"]


def test_sync_refuses_a_host_link_folding_the_destination_into_the_source(
    tmp_path, builders, capsys
):
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("x")
    os.symlink(str(src), tmp_path / "link")

    with pytest.raises(SystemExit) as exc:
        _sync(str(src), str(tmp_path / "link" / "inner"))
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err
    assert sorted(os.listdir(src)) == ["sub"]


def test_sync_refuses_a_host_link_standing_as_the_destination(
    tmp_path, builders, capsys
):
    """The endpoint itself was exempt from resolution, not just its parents.

    sync's pin follows a host endpoint link — host paths get no O_NOFOLLOW
    walk — so the destination really was inside the source, and comparing
    the name never showed it. This recursed to the interpreter's limit and
    left ~1000 stray directories inside the source.
    """
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("x")
    os.symlink(str(src / "sub"), tmp_path / "link")

    with pytest.raises(SystemExit) as exc:
        _sync(str(src), str(tmp_path / "link"))
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err
    assert sorted(os.listdir(src / "sub")) == ["f.txt"]


def test_sync_delete_refuses_a_destination_link_to_the_sources_parent(
    tmp_path, builders, capsys
):
    """`--delete` through such a link pruned the source as an orphan of itself.

    box:/a/b relative to box:/a has no counterpart in itself, so the prune
    pass removed it — and every sibling the parent held besides.
    """
    builders.make_container("box")
    parent = tmp_path / "parent"
    src = parent / "a"
    src.mkdir(parents=True)
    (src / "f.txt").write_text("x")
    (parent / "sibling.txt").write_text("keep")
    os.symlink(str(parent), tmp_path / "link")

    with pytest.raises(SystemExit) as exc:
        _sync(str(src), str(tmp_path / "link"), delete=True)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--delete" in err and "inside the destination" in err
    assert sorted(os.listdir(parent)) == ["a", "sibling.txt"]
    assert (src / "f.txt").read_text() == "x"


def test_copy_cannot_be_made_to_write_through_a_planted_hardlink(
    tmp_path, builders
):
    """A hardlink is the one thing O_NOFOLLOW cannot see.

    The rootfs and the Termux prefix share a filesystem and a uid, so a
    guest can link any host file it can read into its own rootfs under a
    name a later copy will write — no race needed, the trap simply waits.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    victim = tmp_path / "victim"
    victim.write_text("HOST DATA")
    os.link(victim, os.path.join(rootfs, "dest"))
    payload = tmp_path / "payload"
    payload.write_text("payload")

    _copy(str(payload), "box:/dest")

    assert victim.read_text() == "HOST DATA"
    assert open(os.path.join(rootfs, "dest")).read() == "payload"


def test_sync_cannot_be_made_to_write_through_a_planted_hardlink(
    tmp_path, builders
):
    """Same trap, aimed at the temp name sync writes before renaming."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    victim = tmp_path / "victim"
    victim.write_text("HOST DATA")
    os.link(victim, os.path.join(rootfs, "dest.~pd_sync"))
    payload = tmp_path / "payload"
    payload.write_text("payload")

    _sync(str(payload), "box:/dest")

    assert victim.read_text() == "HOST DATA"
    assert open(os.path.join(rootfs, "dest")).read() == "payload"
    assert not os.path.exists(os.path.join(rootfs, "dest.~pd_sync"))


def test_move_across_devices_cannot_write_through_a_planted_hardlink(
    tmp_path, builders, monkeypatch
):
    """The EXDEV fallback copies by hand, so it needs the same protection."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    victim = tmp_path / "victim"
    victim.write_text("HOST DATA")
    os.link(victim, os.path.join(rootfs, "dest"))
    payload = tmp_path / "payload"
    payload.write_text("payload")

    def no_rename(*_a, **_kw):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", no_rename)
    _copy(str(payload), "box:/dest", move=True)

    assert victim.read_text() == "HOST DATA"
    assert open(os.path.join(rootfs, "dest")).read() == "payload"
    assert not payload.exists()


def test_copy_out_of_a_container_cannot_be_folded_back_into_it(
    tmp_path, builders, capsys
):
    """A host link pointing *into* the rootfs folds a copy-out back in.

    The source being fully resolved buys nothing when the destination is a
    host path that never gets walked.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    data = os.path.join(rootfs, "data")
    os.makedirs(os.path.join(data, "sub"))
    with open(os.path.join(data, "sub", "f.txt"), "w") as fh:
        fh.write("x")
    os.symlink(data, tmp_path / "backdoor")

    with pytest.raises(SystemExit) as exc:
        _copy("box:/data", str(tmp_path / "backdoor" / "inner"),
              recursive=True)
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err
    assert sorted(os.listdir(data)) == ["sub"]


def test_move_onto_a_dir_replaces_a_planted_link_at_the_appended_name(
    tmp_path, builders
):
    """`copy --move f box:/dir` renames onto box:/dir/f, link or not.

    The appended base name went through the full chroot walk, so a guest
    that planted box:/dir/f as a symlink redirected the move elsewhere in
    its rootfs and kept the link. mv replaces what is there.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    os.makedirs(os.path.join(rootfs, "dir"))
    with open(os.path.join(rootfs, "victim"), "w") as fh:
        fh.write("UNTOUCHED")
    os.symlink("/victim", os.path.join(rootfs, "dir", "f"))

    payload = tmp_path / "f"
    payload.write_text("NEW")
    _copy(str(payload), "box:/dir", move=True)

    landed = os.path.join(rootfs, "dir", "f")
    assert not os.path.islink(landed)
    assert open(landed).read() == "NEW"
    assert open(os.path.join(rootfs, "victim")).read() == "UNTOUCHED"


def test_guest_filenames_cannot_emit_terminal_escapes(tmp_path, builders,
                                                      capsys):
    """Names in a rootfs are the guest's; -v and the skip warning print them."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = os.path.join(rootfs, "src")
    os.makedirs(src)
    with open(os.path.join(src, "a\x1b[31mRED\x1b[0m"), "w") as fh:
        fh.write("x")
    os.mkfifo(os.path.join(src, "b\x1b[5mBLINK\x1b[0m"))

    _copy("box:/src", str(tmp_path / "out"), recursive=True, verbose=True)

    err = capsys.readouterr().err
    assert "\x1b[31m" not in err
    assert "\x1b[5m" not in err
    assert "a\\e[31mRED\\e[0m" in err
    assert "b\\e[5mBLINK\\e[0m" in err
