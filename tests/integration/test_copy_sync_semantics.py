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


def _maps_holes(path):
    """True when this filesystem will report *path*'s hole map."""
    fd = os.open(path, os.O_RDONLY)
    try:
        size = os.fstat(fd).st_size
        extents = dirfd._data_extents(fd, size)
        return extents is not None and sum(e - s for s, e in extents) < size
    finally:
        os.close(fd)


def test_sparse_copy_matches_the_source_extent_for_extent(tmp_path,
                                                          builders):
    """Reading cannot find a hole the copy buffer is bigger than.

    Zero-scanning could only leave a hole where a whole 256 KiB buffer
    read back zero and was aligned to one, so four bytes at either end of
    a 9 MiB file turned 16 blocks into 520. SEEK_DATA/SEEK_HOLE give the
    map exactly; the scan stays as the fallback for a filesystem that
    will not answer.
    """
    builders.make_container("box")
    src = tmp_path / "head-and-tail"
    with open(src, "wb") as fh:
        fh.write(b"HEAD")
        fh.seek(9 * 1024 * 1024)
        fh.write(b"TAIL")
    if not _maps_holes(src):
        pytest.skip("filesystem does not report holes")

    _copy(str(src), "box:/f")

    out = os.path.join(container_rootfs("box"), "f")
    assert open(out, "rb").read() == open(src, "rb").read()
    assert os.stat(out).st_blocks == os.stat(src).st_blocks


def test_sparse_copy_invents_no_holes_in_a_dense_file(tmp_path, builders):
    """A file with no holes must come out with none."""
    builders.make_container("box")
    src = tmp_path / "dense"
    src.write_bytes(os.urandom(1024 * 1024))

    _copy(str(src), "box:/dense")

    out = os.path.join(container_rootfs("box"), "dense")
    assert open(out, "rb").read() == open(src, "rb").read()
    assert os.stat(out).st_blocks >= os.stat(src).st_blocks


def test_data_extents_restores_the_file_position(tmp_path):
    """Probing seeks; a caller that falls back to reading starts at zero."""
    src = tmp_path / "f"
    with open(src, "wb") as fh:
        fh.write(b"A" * 4096)
        fh.seek(4096 + 8 * 1024 * 1024)
        fh.write(b"B" * 4096)

    fd = os.open(src, os.O_RDONLY)
    try:
        dirfd._data_extents(fd, os.fstat(fd).st_size)
        assert os.lseek(fd, 0, os.SEEK_CUR) == 0
    finally:
        os.close(fd)


def test_sparse_copy_falls_back_when_the_map_is_refused(tmp_path, builders,
                                                        monkeypatch):
    """Not every filesystem implements SEEK_HOLE; the copy must not care."""
    builders.make_container("box")
    src = tmp_path / "f"
    with open(src, "wb") as fh:
        fh.write(b"A" * 4096)
        fh.truncate(16 * 1024 * 1024)

    monkeypatch.setattr(dirfd, "_data_extents", lambda fd, size: None)
    _copy(str(src), "box:/f")

    out = os.path.join(container_rootfs("box"), "f")
    st = os.stat(out)
    assert open(out, "rb").read() == open(src, "rb").read()
    assert st.st_size == 16 * 1024 * 1024
    assert st.st_blocks * 512 < st.st_size    # still sparse, just coarsely


def test_sparse_copy_distrusts_an_all_data_answer(tmp_path, builders,
                                                  monkeypatch):
    """The kernel's generic fallback says "it is all data" for any file.

    That is indistinguishable from a file that really is dense, so an
    answer accounting for the whole length is treated as no answer and
    the zero scan runs instead — otherwise a filesystem without support
    would silently lose the sparseness the scan used to preserve.
    """
    builders.make_container("box")
    src = tmp_path / "f"
    with open(src, "wb") as fh:
        fh.truncate(8 * 1024 * 1024)

    monkeypatch.setattr(dirfd, "_data_extents", lambda fd, size: [(0, size)])
    _copy(str(src), "box:/f")

    out = os.path.join(container_rootfs("box"), "f")
    st = os.stat(out)
    assert open(out, "rb").read() == open(src, "rb").read()
    assert st.st_blocks * 512 < st.st_size


# ----- one entry is one entry: sync steps over what it cannot write -------

def test_sync_keeps_going_when_a_symlink_cannot_be_written(tmp_path,
                                                           builders,
                                                           monkeypatch):
    """A destination that will not hold a symlink must cost one entry.

    vfat holds no symlinks at all, and vfat is what /sdcard is, so
    `sync box:/etc /sdcard/backup` met this on the first link it reached
    and stopped there — everything after it silently untransferred behind
    a single line of output. Reported and skipped now, as `copy -r`
    already did with the same tree.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "aaa").write_text("a")
    os.symlink("aaa", src / "mmm")          # sorts between the two files
    (src / "zzz").write_text("z")

    def no_symlinks(*args, **kwargs):
        raise PermissionError(errno.EPERM, "Operation not permitted")

    monkeypatch.setattr(os, "symlink", no_symlinks)
    code = _exit_code(lambda: _sync(str(src), "box:/dest"))

    dest = os.path.join(rootfs, "dest")
    assert code == 1
    assert sorted(os.listdir(dest)) == ["aaa", "zzz"]


def test_sync_keeps_going_when_a_directory_cannot_be_created(tmp_path,
                                                             builders,
                                                             monkeypatch):
    """Same for a subdirectory, whose subtree is then left alone."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = tmp_path / "src"
    (src / "mmm").mkdir(parents=True)
    (src / "mmm" / "inner").write_text("i")
    (src / "aaa").write_text("a")
    (src / "zzz").write_text("z")

    real_mkdir = os.mkdir

    def failing(name, *args, **kwargs):
        if name == "mmm":
            raise PermissionError(errno.EACCES, "Permission denied")
        return real_mkdir(name, *args, **kwargs)

    monkeypatch.setattr(os, "mkdir", failing)
    code = _exit_code(lambda: _sync(str(src), "box:/dest"))
    monkeypatch.setattr(os, "mkdir", real_mkdir)

    dest = os.path.join(rootfs, "dest")
    assert code == 1
    assert sorted(os.listdir(dest)) == ["aaa", "zzz"]


def test_sync_delete_reports_an_orphan_it_cannot_remove(tmp_path, builders,
                                                        monkeypatch):
    """A prune that cannot finish is one failed entry, not a dead command."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "keep").write_text("k")
    dest = os.path.join(rootfs, "dest")
    os.makedirs(dest)
    for name in ("orphan1", "orphan2"):
        with open(os.path.join(dest, name), "w") as fh:
            fh.write("o")

    real_unlink = os.unlink

    def failing(name, *args, **kwargs):
        if name == "orphan1":
            raise PermissionError(errno.EPERM, "Operation not permitted")
        return real_unlink(name, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", failing)
    code = _exit_code(lambda: _sync(str(src), "box:/dest", delete=True))
    monkeypatch.setattr(os, "unlink", real_unlink)

    assert code == 1
    assert sorted(os.listdir(dest)) == ["keep", "orphan1"]


# ----- --delete leaves the metadata the mirror settled -------------------

def test_sync_delete_keeps_the_mode_of_a_directory_it_pruned(tmp_path,
                                                             builders):
    """make_writable's u+rwx must not outlive the removal it enabled."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = tmp_path / "src"
    (src / "ro").mkdir(parents=True)
    dest = os.path.join(rootfs, "dest", "ro")
    os.makedirs(dest)
    with open(os.path.join(dest, "orphan"), "w") as fh:
        fh.write("o")
    os.chmod(src / "ro", 0o555)

    _sync(str(src), "box:/dest", delete=True)

    assert not os.path.exists(os.path.join(dest, "orphan"))
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o555


def test_sync_delete_keeps_the_mtime_of_a_directory_it_pruned(tmp_path,
                                                              builders):
    """The prune runs after _apply_dir_metadata, and removing bumps mtime.

    Only the root was put right afterwards (_sync_directory does it last of
    all), so every subdirectory an orphan happened to sit in came out
    stamped with the moment of the sync instead of the source's — in a
    command whose contract is that timestamps are preserved.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = tmp_path / "src"
    (src / "d").mkdir(parents=True)
    (src / "d" / "keep").write_text("k")
    dest = os.path.join(rootfs, "dest", "d")
    os.makedirs(dest)
    with open(os.path.join(dest, "orphan"), "w") as fh:
        fh.write("o")
    os.utime(src / "d", (1_000_000_000, 1_000_000_000))

    _sync(str(src), "box:/dest", delete=True)

    assert not os.path.exists(os.path.join(dest, "orphan"))
    assert int(os.stat(dest).st_mtime) == 1_000_000_000


# ----- what is skipped is said out loud ----------------------------------

def test_sync_warns_about_a_special_file_it_skips(tmp_path, builders,
                                                  capsys):
    """`copy -r` warned; sync left the user to diff the tree and find out."""
    builders.make_container("box")
    src = tmp_path / "src"
    src.mkdir()
    os.mkfifo(src / "pipe")
    (src / "ok").write_text("o")

    _sync(str(src), "box:/dest")

    dest = os.path.join(container_rootfs("box"), "dest")
    assert sorted(os.listdir(dest)) == ["ok"]
    assert "skipping special file" in capsys.readouterr().err


# ----- --checksum settles the timestamps it decided not to rewrite -------

def test_sync_checksum_brings_the_mtime_along(tmp_path, builders):
    """Matching content is not a reason to leave the timestamp behind.

    rsync -c updates it; here nothing did, so the destination kept a stale
    mtime that the *next* plain run then rewrote the whole file over.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = tmp_path / "src"
    src.mkdir()
    (src / "f").write_text("same")
    dest = os.path.join(rootfs, "dest")
    os.makedirs(dest)
    with open(os.path.join(dest, "f"), "w") as fh:
        fh.write("same")
    os.utime(src / "f", (1_000_000_000, 1_000_000_000))
    os.utime(os.path.join(dest, "f"), (1_500_000_000, 1_500_000_000))

    _sync(str(src), "box:/dest", checksum=True)

    assert int(os.stat(os.path.join(dest, "f")).st_mtime) == 1_000_000_000


# ----- copy -r merges, as cp -a does -------------------------------------

def test_copy_recursive_merges_into_a_tree_that_is_already_there(tmp_path,
                                                                 builders):
    """Running the same copy twice updates the destination.

    mkdirat refuses to create over anything, so the second run died on
    EEXIST at the top of the tree — a copy that cannot be repeated is not
    the `cp -a` the command advertises.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f").write_text("one")
    os.symlink("f", src / "sub" / "link")
    dest = os.path.join(rootfs, "dest")
    os.makedirs(dest)

    _copy(str(src), "box:/dest", recursive=True)
    (src / "sub" / "f").write_text("two")
    (src / "sub" / "new").write_text("n")
    _copy(str(src), "box:/dest", recursive=True)

    landed = os.path.join(dest, "src", "sub")
    assert sorted(os.listdir(landed)) == ["f", "link", "new"]
    assert open(os.path.join(landed, "f")).read() == "two"
    assert os.readlink(os.path.join(landed, "link")) == "f"


def test_copy_recursive_merge_refuses_a_type_conflict(tmp_path, builders,
                                                      capsys):
    """cp will not put a directory where a file is, or the reverse."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = tmp_path / "src"
    (src / "sub").mkdir(parents=True)
    (src / "sub" / "f").write_text("s")
    (src / "plain").write_text("p")
    dest = os.path.join(rootfs, "dest", "src")
    os.makedirs(dest)
    with open(os.path.join(dest, "sub"), "w") as fh:     # file vs directory
        fh.write("in the way")
    os.makedirs(os.path.join(dest, "plain"))             # directory vs file

    code = _exit_code(lambda: _copy(str(src), "box:/dest", recursive=True))

    assert code == 1
    assert open(os.path.join(dest, "sub")).read() == "in the way"
    assert os.path.isdir(os.path.join(dest, "plain"))
    assert "cannot copy" in capsys.readouterr().err


def test_move_refuses_a_populated_destination_directory(tmp_path, builders,
                                                        monkeypatch):
    """The cross-device fallback keeps rename(2)'s rule, not cp's.

    A same-device move gets ENOTEMPTY from the kernel; the fallback has to
    refuse it too, or `--move` would mean one thing on a phone's internal
    storage and another onto /sdcard.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = os.path.join(rootfs, "src")
    os.makedirs(src)
    with open(os.path.join(src, "f"), "w") as fh:
        fh.write("s")
    dest = os.path.join(rootfs, "dest")
    os.makedirs(dest)
    with open(os.path.join(dest, "occupied"), "w") as fh:
        fh.write("d")

    real_rename = os.rename

    def no_rename(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", no_rename)
    code = _exit_code(lambda: _copy("box:/src", "box:/dest/occupied",
                                    move=True))
    monkeypatch.setattr(os, "rename", real_rename)

    assert code == 1
    assert os.path.isfile(os.path.join(src, "f"))       # source untouched


def test_move_across_devices_keeps_the_source_when_a_fifo_is_skipped(
    tmp_path, builders, monkeypatch
):
    """No tree this module writes carries a FIFO, so a move must not delete.

    The skip is a warning during a copy and silent data loss during a move,
    and on Termux the common move — a rootfs directory onto /sdcard — is
    exactly the cross-device one that takes this path.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    src = os.path.join(rootfs, "src")
    os.makedirs(src)
    with open(os.path.join(src, "real"), "w") as fh:
        fh.write("r")
    os.mkfifo(os.path.join(src, "pipe"))

    real_rename = os.rename

    def no_rename(*args, **kwargs):
        raise OSError(errno.EXDEV, "Invalid cross-device link")

    monkeypatch.setattr(os, "rename", no_rename)
    code = _exit_code(lambda: _copy("box:/src", "box:/moved", move=True))
    monkeypatch.setattr(os, "rename", real_rename)

    assert code == 1
    assert sorted(os.listdir(src)) == ["pipe", "real"]
    assert sorted(os.listdir(os.path.join(rootfs, "moved"))) == ["real"]
