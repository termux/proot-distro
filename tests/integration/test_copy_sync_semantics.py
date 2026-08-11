# Behavioural tests for `copy` / `sync` that are not about symlinks: what
# gets transferred, what metadata comes with it, what is pruned, and what
# the exit status says afterwards. The reference in every case is what
# `cp -r` and `rsync -a` do with the same inputs.

import contextlib
import errno
import os
import stat
from types import SimpleNamespace

import pytest

from proot_distro import dirfd
from proot_distro.commands import sync as sync_mod
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


def _exit_code(fn):
    """Run *fn* and return the status it exited with, 0 for a clean return."""
    try:
        fn()
    except SystemExit as exc:
        return exc.code
    return 0


# ----- --delete decides from what the source really holds ------------------

def test_sync_delete_keeps_an_entry_that_appeared_mid_transfer(tmp_path,
                                                               builders):
    """The counting pass is not the last word on what the source contains.

    src_rels was filled once, before the mirror pass ran, so an entry
    created in between was transferred by the mirror and then removed by
    the prune as an orphan — the sync deleted the file it had just
    written. A container source is live by definition, so this needs no
    contrivance beyond the timing.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")

    real = sync_mod._mirror_entries
    fired = []

    def racing(src_fd, dst_fd, rel, ctx):
        if not fired:
            fired.append(True)
            (src / "late.txt").write_text("late")
        return real(src_fd, dst_fd, rel, ctx)

    sync_mod._mirror_entries = racing
    try:
        _sync(str(src), "box:/d", delete=True)
    finally:
        sync_mod._mirror_entries = real

    assert fired
    dst = os.path.join(container_rootfs("box"), "d")
    assert sorted(os.listdir(dst)) == ["a.txt", "late.txt"]


def test_sync_delete_declines_when_the_source_root_cannot_be_listed(
    tmp_path, builders, monkeypatch, capsys
):
    """An enumeration that failed is not evidence of an empty source.

    A failed listing of the *root* leaves src_rels empty, and skipped_rels
    cannot express "all of it" — every relative path is below the root. So
    every destination entry looked like an orphan and the prune emptied
    the lot. rsync disables --delete on an I/O error for the same reason.
    """
    builders.make_container("box")
    dst = os.path.join(container_rootfs("box"), "dst")
    os.makedirs(os.path.join(dst, "keepdir"))
    with open(os.path.join(dst, "important.txt"), "w") as fh:
        fh.write("IMPORTANT")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")

    real = dirfd.listdir_at
    failed = []

    def flaky(fd):
        # The first listing is the source root. EMFILE is what a deep
        # tree under a low descriptor limit actually produces here.
        if not failed:
            failed.append(True)
            raise OSError(errno.EMFILE, "Too many open files")
        return real(fd)

    monkeypatch.setattr(dirfd, "listdir_at", flaky)
    monkeypatch.setattr(sync_mod.dirfd, "listdir_at", flaky)
    code = _exit_code(lambda: _sync(str(src), "box:/dst", delete=True))
    monkeypatch.undo()

    assert code == 1
    assert "not deleting anything" in capsys.readouterr().err
    assert sorted(os.listdir(dst)) == ["a.txt", "important.txt", "keepdir"]


def test_sync_delete_needs_a_directory_source(tmp_path, builders, capsys):
    """Nothing is enumerated for a single file, so nothing was ever pruned."""
    builders.make_container("box")
    os.makedirs(os.path.join(container_rootfs("box"), "d"))
    src = tmp_path / "one.txt"
    src.write_text("one")

    with pytest.raises(SystemExit) as exc:
        _sync(str(src), "box:/d", delete=True)
    assert exc.value.code == 1
    assert "--delete" in capsys.readouterr().err


# ----- one bad entry must not cost the rest of the tree --------------------

def test_copy_recursive_steps_over_an_unreadable_directory(tmp_path,
                                                           builders, capsys):
    """`cp -r` reports and carries on; letting EACCES propagate did not.

    The transfer stopped at the first locked directory, so everything
    after it in the walk was silently left uncopied — the opposite of what
    someone reaching for a recursive copy of a rootfs wants.
    """
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "locked").mkdir(parents=True)
    (src / "locked" / "secret.txt").write_text("s")
    (src / "zafter.txt").write_text("after")
    os.chmod(src / "locked", 0o000)
    try:
        code = _exit_code(lambda: _copy(str(src), "box:/d", recursive=True))
    finally:
        os.chmod(src / "locked", 0o755)

    assert code == 1
    err = capsys.readouterr().err
    # Named on the source side: that is the path that could not be read,
    # and a destination that was never written reads as the wrong fault.
    assert f"cannot copy '{src}/locked'" in err
    dest = os.path.join(container_rootfs("box"), "d")
    # The directory itself is created, empty, and the walk resumes after
    # it: exactly what cp -r leaves behind.
    assert sorted(os.listdir(dest)) == ["locked", "zafter.txt"]
    assert os.listdir(os.path.join(dest, "locked")) == []


def test_move_across_devices_keeps_a_source_it_could_not_fully_copy(
    tmp_path, builders, monkeypatch, capsys
):
    """Stepping over an entry must not turn --move into data loss."""
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "locked").mkdir(parents=True)
    (src / "locked" / "secret.txt").write_text("s")
    (src / "ok.txt").write_text("ok")
    os.chmod(src / "locked", 0o000)

    def no_rename(*a, **kw):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", no_rename)
    try:
        code = _exit_code(lambda: _copy(str(src), "box:/moved", move=True))
    finally:
        monkeypatch.undo()
        os.chmod(src / "locked", 0o755)

    assert code == 1
    assert "Source left in place" in capsys.readouterr().err
    assert (src / "locked" / "secret.txt").read_text() == "s"
    assert (src / "ok.txt").read_text() == "ok"


def test_sync_reports_a_skipped_source_directory_in_its_exit_status(
    tmp_path, builders
):
    """Coming back 0 after skipping part of the source misleads a script."""
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "locked").mkdir(parents=True)
    (src / "ok.txt").write_text("ok")
    os.chmod(src / "locked", 0o000)
    try:
        code = _exit_code(lambda: _sync(str(src), "box:/d"))
    finally:
        os.chmod(src / "locked", 0o755)

    assert code == 1
    dest = os.path.join(container_rootfs("box"), "d")
    assert sorted(os.listdir(dest)) == ["locked", "ok.txt"]


def test_sync_leaves_no_temp_file_when_interrupted(tmp_path, builders,
                                                   monkeypatch):
    """Ctrl-C is not an OSError, and the cleanup only caught OSError."""
    builders.make_container("box")
    dst = os.path.join(container_rootfs("box"), "dst")
    os.makedirs(dst)
    with open(os.path.join(dst, "a.txt"), "w") as fh:
        fh.write("old")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("brand new content")

    real = dirfd.copy_data

    def interrupted(sfd, dfd, src_st=None):
        real(sfd, dfd, src_st)
        raise KeyboardInterrupt

    monkeypatch.setattr(dirfd, "copy_data", interrupted)
    with contextlib.suppress(SystemExit, KeyboardInterrupt):
        _sync(str(src), "box:/dst")
    monkeypatch.undo()

    assert sorted(os.listdir(dst)) == ["a.txt"]
    assert open(os.path.join(dst, "a.txt")).read() == "old"


# ----- metadata ------------------------------------------------------------

def test_directory_modes_and_times_survive_both_commands(tmp_path, builders):
    """"Modes and timestamps are preserved" has to hold for directories.

    sync applied a directory's mode but never its mtime, and gave the
    destination root neither, so a synced tree came back stamped with the
    moment of the sync.
    """
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("f")
    os.chmod(src / "sub", 0o751)
    os.utime(src / "sub", (1500000, 1500000))
    os.chmod(src, 0o701)
    os.utime(src, (1400000, 1400000))

    _copy(str(src), "box:/c", recursive=True)
    _sync(str(src), "box:/s")

    rootfs = container_rootfs("box")
    try:
        for label in ("c", "s"):
            root = os.path.join(rootfs, label)
            st_root = os.stat(root)
            st_sub = os.stat(os.path.join(root, "sub"))
            assert stat.S_IMODE(st_root.st_mode) == 0o701, label
            assert int(st_root.st_mtime) == 1400000, label
            assert stat.S_IMODE(st_sub.st_mode) == 0o751, label
            assert int(st_sub.st_mtime) == 1500000, label
    finally:
        for label in ("c", "s"):
            os.chmod(os.path.join(rootfs, label), 0o755)


def test_sync_applies_a_mode_only_change_to_a_regular_file(tmp_path,
                                                           builders):
    """A `chmod +x` with no content change was invisible for good.

    _needs_update compares type, size and mtime — never permissions — so
    nothing was rewritten and nothing fixed the mode either. Directories
    got theirs on every pass, which kept the gap quiet.
    """
    builders.make_container("box")
    src = tmp_path / "tree"
    src.mkdir()
    script = src / "script.sh"
    script.write_text("#!/bin/sh\n")
    os.chmod(script, 0o644)
    os.utime(script, (1400000, 1400000))
    _sync(str(src), "box:/d")

    os.chmod(script, 0o755)
    os.utime(script, (1400000, 1400000))     # only the mode moved
    _sync(str(src), "box:/d")

    dst = os.path.join(container_rootfs("box"), "d", "script.sh")
    assert stat.S_IMODE(os.stat(dst).st_mode) == 0o755
    assert open(dst).read() == "#!/bin/sh\n"


def test_sparse_files_are_not_materialised(tmp_path, builders):
    """A hole read back as zeros is written back as a hole.

    A rootfs's /var/log/lastlog is sparse and nominally enormous, its
    length following the highest uid on the system. Writing every zero
    out turned copying one into filling the device.
    """
    builders.make_container("box")
    src = tmp_path / "big"
    with open(src, "wb") as fh:
        fh.truncate(64 * 1024 * 1024)
    assert os.stat(src).st_blocks * 512 < os.stat(src).st_size

    _copy(str(src), "box:/big")
    _sync(str(src), "box:/big2")

    for name in ("big", "big2"):
        st = os.stat(os.path.join(container_rootfs("box"), name))
        assert st.st_size == 64 * 1024 * 1024
        assert st.st_blocks * 512 < st.st_size // 2, name


def test_a_file_of_holes_and_data_round_trips(tmp_path, builders):
    """The zero-skipping path must not lose or misplace real content."""
    builders.make_container("box")
    src = tmp_path / "mixed"
    with open(src, "wb") as fh:
        fh.truncate(8 * 1024 * 1024)
        fh.seek(5 * 1024 * 1024)
        fh.write(b"PAYLOAD")
        fh.truncate(16 * 1024 * 1024)       # ends in a hole

    _copy(str(src), "box:/mixed")

    dst = os.path.join(container_rootfs("box"), "mixed")
    assert open(dst, "rb").read() == open(src, "rb").read()
    assert os.stat(dst).st_size == 16 * 1024 * 1024


# ----- messages and refusals ----------------------------------------------

def test_sync_refuses_a_special_file_as_the_whole_source(tmp_path, builders,
                                                         capsys):
    """It returned without a word and then reported success."""
    builders.make_container("box")
    fifo = tmp_path / "pipe"
    os.mkfifo(str(fifo))

    with pytest.raises(SystemExit) as exc:
        _sync(str(fifo), "box:/x")
    assert exc.value.code == 1
    assert "not a regular file or directory" in capsys.readouterr().err
    assert not os.path.lexists(os.path.join(container_rootfs("box"), "x"))


def test_sync_names_the_destination_the_same_way_throughout(tmp_path,
                                                            builders,
                                                            capsys):
    """Deletions printed the rootfs path where writes printed the spec."""
    builders.make_container("box")
    dst = os.path.join(container_rootfs("box"), "d")
    os.makedirs(dst)
    with open(os.path.join(dst, "stale.txt"), "w") as fh:
        fh.write("stale")
    src = tmp_path / "src"
    src.mkdir()
    (src / "new.txt").write_text("new")

    _sync(str(src), "box:/d", delete=True, verbose=True)

    lines = [ln for ln in capsys.readouterr().err.splitlines()
             if "New file" in ln or "Delete" in ln]
    assert len(lines) == 2
    for line in lines:
        assert "box:/d/" in line
        assert container_rootfs("box") not in line


def test_move_across_devices_logs_each_file_when_verbose(tmp_path, builders,
                                                         monkeypatch, capsys):
    """The fallback hardcoded verbose=False, so -v printed nothing."""
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("f")

    def no_rename(*a, **kw):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", no_rename)
    _copy(str(src), "box:/moved", move=True, verbose=True)
    monkeypatch.undo()

    assert [ln for ln in capsys.readouterr().err.splitlines()
            if "Copying:" in ln]


def test_copy_recursive_draws_progress_against_a_real_total(tmp_path,
                                                            builders,
                                                            monkeypatch):
    """`sync` had a bar and `copy` had none, on the same size of job."""
    import proot_distro.commands.copy as copy_mod
    builders.make_container("box")
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    for i in range(5):
        (src / "sub" / f"f{i}.txt").write_text("x")

    ticks = []
    monkeypatch.setattr(copy_mod, "draw_count_bar",
                        lambda done, total, **kw: ticks.append((done, total)))
    # The counting walk is skipped when nothing would be drawn, which off
    # a TTY is always; say a bar is wanted so the accounting is exercised.
    monkeypatch.setattr(copy_mod, "progress_active", lambda: True)
    _copy(str(src), "box:/d", recursive=True)

    # Five files; the directory holding them is walked but not counted,
    # since copy_tree_at reports a directory only once it is finished.
    assert ticks[-1] == (5, 5)


def test_copy_recursive_skips_the_counting_walk_with_no_bar_to_draw(
    tmp_path, builders, monkeypatch
):
    """Counting is a whole extra walk of the source; do not pay for it."""
    import proot_distro.commands.copy as copy_mod
    builders.make_container("box")
    src = tmp_path / "tree"
    src.mkdir()
    (src / "f.txt").write_text("x")

    counted = []
    real = dirfd.count_tree_at
    monkeypatch.setattr(dirfd, "count_tree_at",
                        lambda fd: counted.append(True) or real(fd))
    monkeypatch.setattr(copy_mod, "progress_active", lambda: True)

    _copy(str(src), "box:/verbose", recursive=True, verbose=True)
    assert not counted, "--verbose prints per entry, so no bar is drawn"

    monkeypatch.setattr(copy_mod, "progress_active", lambda: False)
    _copy(str(src), "box:/quiet", recursive=True)
    assert not counted, "nothing is drawn off a TTY either"


# ----- the accounting the two passes share ---------------------------------

def test_one_bad_entry_is_counted_once(tmp_path, builders, monkeypatch,
                                       capsys):
    """Both passes meet the same tree, so both meet the same bad entry.

    Counting it in each made one unreadable file report as "2 entries
    could not be transferred"; note_failure is keyed on the relative path
    and ignores a repeat.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "bad").write_text("b")
    (src / "ok.txt").write_text("o")

    real = dirfd.lstat_at

    def flaky(fd, name):
        if name == "bad":
            raise OSError(errno.EIO, "Input/output error")
        return real(fd, name)

    monkeypatch.setattr(dirfd, "lstat_at", flaky)
    monkeypatch.setattr(sync_mod.dirfd, "lstat_at", flaky)
    code = _exit_code(lambda: _sync(str(src), "box:/d"))
    monkeypatch.undo()

    assert code == 1
    assert "1 entry could not be transferred" in capsys.readouterr().err


def test_sync_progress_total_keeps_up_with_a_growing_source(tmp_path,
                                                            builders):
    """The count outran the total it was measured against.

    The total is fixed by the counting pass but the mirror pass counts
    every entry it writes, so a source that gained files in between drove
    the display to "(5/1)" and a bar wider than its twenty cells.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")

    ticks = []
    real_bar = sync_mod.draw_count_bar
    sync_mod.draw_count_bar = lambda d, t, **kw: ticks.append((d, t))
    real = sync_mod._mirror_entries
    fired = []

    def racing(src_fd, dst_fd, rel, ctx):
        if not fired:
            fired.append(True)
            for i in range(4):
                (src / f"late{i}.txt").write_text("x")
        return real(src_fd, dst_fd, rel, ctx)

    sync_mod._mirror_entries = racing
    try:
        _sync(str(src), "box:/d")
    finally:
        sync_mod._mirror_entries = real
        sync_mod.draw_count_bar = real_bar

    assert fired and ticks
    assert not [t for t in ticks if t[0] > t[1]], ticks


def test_sync_reports_a_level_only_the_mirror_pass_could_not_read(
    tmp_path, builders, capsys
):
    """A subtree readable when counted and not when mirrored went quiet.

    _mirror_entries treated every listing failure as "already reported by
    _collect_rels", so one the counting pass never saw left the
    destination stale, said nothing, and exited 0.
    """
    builders.make_container("box")
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f.txt").write_text("new content")
    dst = os.path.join(container_rootfs("box"), "d")
    os.makedirs(os.path.join(dst, "sub"))
    with open(os.path.join(dst, "sub", "f.txt"), "w") as fh:
        fh.write("stale")

    real = dirfd.listdir_at
    seen = {"n": 0}

    def flaky(fd):
        names = real(fd)
        if "f.txt" in names:
            seen["n"] += 1
            if seen["n"] == 2:      # pass 1 through, pass 2 fails
                raise OSError(errno.EIO, "Input/output error")
        return names

    dirfd.listdir_at = flaky
    sync_mod.dirfd.listdir_at = flaky
    try:
        code = _exit_code(lambda: _sync(str(src), "box:/d"))
    finally:
        dirfd.listdir_at = real
        sync_mod.dirfd.listdir_at = real

    assert code == 1
    assert "cannot read directory" in capsys.readouterr().err
    assert open(os.path.join(dst, "sub", "f.txt")).read() == "stale"


def test_copy_names_a_failed_root_without_a_trailing_slash(tmp_path,
                                                           builders, capsys):
    """os.path.join(src, "") is "src/", which reads as a stray path."""
    builders.make_container("box")
    src = tmp_path / "tree"
    src.mkdir()
    (src / "f.txt").write_text("f")

    real = dirfd.listdir_at

    def boom(fd):
        raise OSError(errno.EIO, "Input/output error")

    dirfd.listdir_at = boom
    try:
        code = _exit_code(lambda: _copy(str(src), "box:/d", recursive=True))
    finally:
        dirfd.listdir_at = real

    assert code == 1
    assert f"cannot copy '{src}'" in capsys.readouterr().err


def test_sync_keeps_a_read_only_source_directory_working_twice(tmp_path,
                                                               builders):
    """The root's mode is applied last, and re-opened writable next run."""
    builders.make_container("box")
    src = tmp_path / "ro"
    src.mkdir()
    (src / "f.txt").write_text("data")
    os.chmod(src, 0o555)
    dst = os.path.join(container_rootfs("box"), "d")
    try:
        _sync(str(src), "box:/d")
        assert stat.S_IMODE(os.stat(dst).st_mode) == 0o555

        os.chmod(src, 0o755)
        (src / "f.txt").write_text("data, revised and longer")
        os.chmod(src, 0o555)

        _sync(str(src), "box:/d")
        assert stat.S_IMODE(os.stat(dst).st_mode) == 0o555
        assert open(os.path.join(dst, "f.txt")).read() == \
            "data, revised and longer"
    finally:
        os.chmod(src, 0o755)
        with contextlib.suppress(OSError):
            os.chmod(dst, 0o755)


@pytest.mark.parametrize("shape", ["all-hole", "head-and-tail",
                                   "buffer-boundary", "empty"])
def test_sparse_shapes_round_trip_byte_for_byte(tmp_path, builders, shape):
    """The zero-skipping path must never move or drop real content."""
    builders.make_container("box")
    src = tmp_path / shape
    with open(src, "wb") as fh:
        if shape == "all-hole":
            fh.truncate(4096)
        elif shape == "head-and-tail":
            fh.write(b"HEAD")
            fh.seek(9 * 1024 * 1024)
            fh.write(b"TAIL")
        elif shape == "buffer-boundary":
            fh.truncate(256 * 1024)         # exactly one copy buffer
            fh.seek(256 * 1024)
            fh.write(b"Z")

    _copy(str(src), f"box:/{shape}")

    out = os.path.join(container_rootfs("box"), shape)
    assert open(out, "rb").read() == open(src, "rb").read()
    assert os.stat(out).st_size == os.stat(src).st_size
