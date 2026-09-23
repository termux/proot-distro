# What a transfer decides an entry *is* and what it stamps on the copy
# have to describe the file whose bytes it reads.
#
# `sync` of a single file took its lstat of the source path before the
# destination was resolved, before the overlap check and before the pin,
# and then judged the entry by it. A source replaced anywhere in between
# was compared by the old file's size and mtime: a newer file of the same
# size came out "up to date", was never transferred, and the command said
# it had finished.
#
# Both commands also stamped the copy with the mode and times of the lstat
# the walk took, a moment before the file was opened for reading. A source
# replaced in that moment had its new content written out under the old
# entry's mode — a 0600 file arrived readable by everyone. The stat that
# describes the bytes is the fstat of the descriptor they are read through.

import os
import stat
from types import SimpleNamespace

import pytest

from proot_distro import dirfd
from proot_distro.commands import sync as sync_mod
from proot_distro.commands.copy import command_copy
from proot_distro.commands.sync import command_sync


def _sync(source, destination, **over):
    base = dict(source=source, destination=destination, verbose=False,
                checksum=False, delete=False)
    base.update(over)
    command_sync(SimpleNamespace(**base))


def _copy(source, destination, **over):
    base = dict(source=source, destination=destination, verbose=False,
                move=False, recursive=False)
    base.update(over)
    command_copy(SimpleNamespace(**base))


def _after_first_pin(monkeypatch, action):
    """Run *action* once the first endpoint has been pinned."""
    real = sync_mod.pin_path
    fired = []

    def pinning(*args, **kwargs):
        pin = real(*args, **kwargs)
        if not fired:
            fired.append(True)
            action()
        return pin

    monkeypatch.setattr(sync_mod, "pin_path", pinning)
    return fired


def _before_opening(monkeypatch, name, action):
    """Run *action* just before the source entry *name* is opened."""
    real = dirfd.open_regular_at
    fired = []

    def opening(dir_fd, entry, flags, *args, **kwargs):
        if entry == name and not fired:
            fired.append(True)
            action()
        return real(dir_fd, entry, flags, *args, **kwargs)

    monkeypatch.setattr(dirfd, "open_regular_at", opening)
    return fired


def _replace(path, content, mode=0o644, mtime=None):
    """Put a different file under *path*, the way `mv new path` does."""
    new = str(path) + ".new"
    with open(new, "w") as fh:
        fh.write(content)
    os.chmod(new, mode)
    if mtime is not None:
        os.utime(new, (mtime, mtime))
    os.rename(new, str(path))


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_single_file_sync_judges_the_file_that_is_there_now(tmp_path,
                                                            monkeypatch):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.write_text("OLD")
    dst.write_text("OLD")
    os.utime(src, (1000, 1000))
    os.utime(dst, (1000, 1000))

    # Same size, newer mtime: only the fresh stat can tell it apart.
    fired = _after_first_pin(
        monkeypatch, lambda: _replace(src, "NEW", mtime=2000))
    _sync(str(src), str(dst))

    assert fired
    assert dst.read_text() == "NEW"
    assert int(os.stat(dst).st_mtime) == 2000


def test_single_file_sync_stamps_the_mode_of_what_it_read(tmp_path,
                                                          monkeypatch):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.write_text("OLD")
    os.chmod(src, 0o644)

    fired = _after_first_pin(
        monkeypatch, lambda: _replace(src, "SECRET", mode=0o600))
    _sync(str(src), str(dst))

    assert fired
    assert dst.read_text() == "SECRET"
    assert _mode(dst) == 0o600


def test_single_file_sync_refuses_a_source_that_became_a_directory(
        tmp_path, monkeypatch, capsys):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.write_text("OLD")

    def to_directory():
        os.unlink(src)
        os.mkdir(src)
        (src / "inside").write_text("x")

    _after_first_pin(monkeypatch, to_directory)
    with pytest.raises(SystemExit) as exc:
        _sync(str(src), str(dst))

    assert exc.value.code == 1
    assert "no longer a regular file" in capsys.readouterr().err
    assert not dst.exists()


def test_single_file_sync_reports_a_source_that_vanished(tmp_path,
                                                         monkeypatch):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.write_text("OLD")

    _after_first_pin(monkeypatch, lambda: os.unlink(src))
    with pytest.raises(SystemExit) as exc:
        _sync(str(src), str(dst))

    assert exc.value.code == 1
    assert not dst.exists()


def test_tree_sync_stamps_the_mode_of_what_it_read(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "f").write_text("OLD")
    os.chmod(src / "f", 0o644)
    out = tmp_path / "out"

    fired = _before_opening(
        monkeypatch, "f", lambda: _replace(src / "f", "SECRET", mode=0o600))
    _sync(str(src), str(out))

    assert fired
    assert (out / "f").read_text() == "SECRET"
    assert _mode(out / "f") == 0o600


def test_tree_sync_stamps_the_mtime_of_what_it_read(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "f").write_text("OLD")
    os.utime(src / "f", (1000, 1000))
    out = tmp_path / "out"

    _before_opening(
        monkeypatch, "f", lambda: _replace(src / "f", "NEWER", mtime=3000))
    _sync(str(src), str(out))

    # The old mtime on the new content would have made the next run
    # compare the two as equal whenever the sizes matched.
    assert (out / "f").read_text() == "NEWER"
    assert int(os.stat(out / "f").st_mtime) == 3000


def test_recursive_copy_stamps_the_mode_of_what_it_read(tmp_path,
                                                        monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "f").write_text("OLD")
    os.chmod(src / "f", 0o644)
    out = tmp_path / "out"

    fired = _before_opening(
        monkeypatch, "f", lambda: _replace(src / "f", "SECRET", mode=0o600))
    _copy(str(src), str(out), recursive=True)

    assert fired
    assert (out / "f").read_text() == "SECRET"
    assert _mode(out / "f") == 0o600


def test_copy_file_at_stamps_the_mode_of_what_it_read(tmp_path, monkeypatch):
    src = tmp_path / "src"
    src.mkdir()
    (src / "f").write_text("OLD")
    os.chmod(src / "f", 0o755)
    dst = tmp_path / "dst"
    dst.mkdir()

    _before_opening(
        monkeypatch, "f", lambda: _replace(src / "f", "SECRET", mode=0o600))
    sfd = dirfd.opendir(str(src))
    dfd = dirfd.opendir(str(dst))
    try:
        dirfd.copy_file_at(sfd, "f", dfd, "f")
    finally:
        os.close(dfd)
        os.close(sfd)

    assert (dst / "f").read_text() == "SECRET"
    assert _mode(dst / "f") == 0o600
