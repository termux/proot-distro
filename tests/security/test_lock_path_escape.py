# Containment tests for the lock files under RUNTIME_DIR/locks.
#
# That directory is guest-writable — on Termux it sits under the
# $TERMUX_PREFIX bound read-write into every non-isolated container — and
# the file names in it are derived from the container name, so they are
# entirely predictable. open(path, "w") therefore followed a planted
# `<name>.lock -> /host/file` and truncated that file before flocking
# anything, and a FIFO under the same name blocked the command outright.

import errno
import os
import stat

import pytest

from proot_distro import locking
from proot_distro.locking import BuildLock, ContainerLock


@pytest.fixture
def locks_dir():
    os.makedirs(locking.LOCKS_DIR, exist_ok=True)
    return locking.LOCKS_DIR


@pytest.fixture
def victim(tmp_path):
    path = tmp_path / "host-file"
    path.write_text("host content\n")
    return path


def _acquire(name="box", exclusive=True, command="test"):
    lock = ContainerLock(name, exclusive=exclusive, command=command)
    result = lock.acquire()
    return lock, result


# --- planted entries -------------------------------------------------------

def test_symlinked_lock_file_is_not_truncated(locks_dir, victim):
    os.symlink(str(victim), os.path.join(locks_dir, "box.lock"))

    lock, ok = _acquire()
    try:
        assert ok is True
        assert victim.read_text() == "host content\n"
        path = locking.container_lock_path("box")
        assert not os.path.islink(path)
        assert stat.S_ISREG(os.lstat(path).st_mode)
        assert open(path).read().split()[1] == "test"
    finally:
        lock.release()


def test_dangling_symlink_creates_no_host_file(locks_dir, tmp_path):
    victim = tmp_path / "not-there-yet"
    os.symlink(str(victim), os.path.join(locks_dir, "box.lock"))

    lock, ok = _acquire()
    try:
        assert ok is True
        assert not victim.exists()
    finally:
        lock.release()


def test_fifo_in_the_way_is_replaced(locks_dir):
    # O_NOFOLLOW says nothing about a FIFO: opening one for writing waits
    # for a reader the guest simply never provides.
    os.mkfifo(os.path.join(locks_dir, "box.lock"))

    lock, ok = _acquire()
    try:
        assert ok is True
        assert stat.S_ISREG(os.lstat(locking.container_lock_path("box")).st_mode)
    finally:
        lock.release()


def test_empty_directory_in_the_way_is_replaced(locks_dir):
    os.mkdir(os.path.join(locks_dir, "box.lock"))

    lock, ok = _acquire()
    try:
        assert ok is True
        assert stat.S_ISREG(os.lstat(locking.container_lock_path("box")).st_mode)
    finally:
        lock.release()


def test_unremovable_entry_fails_closed(locks_dir):
    # A non-empty directory cannot be dropped, and a lock that is being
    # prevented must not pass for one that merely could not be created.
    os.mkdir(os.path.join(locks_dir, "box.lock"))
    open(os.path.join(locks_dir, "box.lock", "keep"), "w").close()

    lock = ContainerLock("box", exclusive=True, command="test")
    assert lock.acquire() is False
    assert lock._hostile == locking.container_lock_path("box")

    with pytest.raises(SystemExit) as exc:
        with ContainerLock("box", exclusive=True, command="test"):
            pass
    assert exc.value.code == 1


def test_build_locks_get_the_same_treatment(victim):
    lock = BuildLock("repo/image:1.0", "aarch64", command="build")
    os.makedirs(os.path.dirname(lock.lock_path), exist_ok=True)
    os.symlink(str(victim), lock.lock_path)

    try:
        assert lock.acquire() is True
        assert victim.read_text() == "host content\n"
        assert not os.path.islink(lock.lock_path)
    finally:
        lock.release()


def test_symlinked_locks_dir_is_replaced(tmp_path):
    # The directory itself, not just the entry in it.
    outside = tmp_path / "outside"
    outside.mkdir()
    os.makedirs(locking.RUNTIME_DIR, exist_ok=True)
    os.symlink(str(outside), locking.LOCKS_DIR)

    lock, ok = _acquire()
    try:
        assert ok is True
        # Nothing went through the link, and the lock is a real one.
        assert os.listdir(str(outside)) == []
        assert not os.path.islink(locking.LOCKS_DIR)
        assert stat.S_ISREG(
            os.lstat(locking.container_lock_path("box")).st_mode)
    finally:
        lock.release()


def test_unremovable_locks_dir_fails_closed(tmp_path):
    os.makedirs(locking.RUNTIME_DIR, exist_ok=True)
    with open(locking.LOCKS_DIR, "w") as fh:
        fh.write("in the way")
    os.chmod(locking.RUNTIME_DIR, 0o500)
    try:
        lock = ContainerLock("box", exclusive=True, command="test")
        assert lock.acquire() is False
        assert lock._hostile == locking.LOCKS_DIR
    finally:
        os.chmod(locking.RUNTIME_DIR, 0o700)


# --- the holder hint -------------------------------------------------------

def test_loser_leaves_the_holders_line_intact(locks_dir):
    holder = ContainerLock("box", exclusive=True, command="install")
    assert holder.acquire() is True
    try:
        # Stand in for a second process: flock conflicts are per open file
        # description, so a fresh handle in this process contends for real.
        locking._held_exclusive.clear()
        loser = ContainerLock("box", exclusive=True, command="login")
        assert loser.acquire() is False
        hint = loser.holder_hint()
        assert f"PID {os.getpid()}" in hint and "install" in hint
        # And the winner's record is still the one on disk.
        assert holder.holder_hint() == hint
    finally:
        locking._held_exclusive.discard(holder.lock_path)
        holder.release()


def test_hint_is_empty_when_the_entry_is_not_a_lock_file(locks_dir, victim):
    victim.write_text("99999999 something\n")
    os.symlink(str(victim), os.path.join(locks_dir, "box.lock"))
    lock = ContainerLock("box", exclusive=True, command="test")
    assert lock.holder_hint() == ""


# --- busy_locks ------------------------------------------------------------

def test_busy_locks_ignores_a_symlinked_entry(locks_dir, victim):
    os.symlink(str(victim), os.path.join(locks_dir, "other.lock"))
    lock, ok = _acquire()
    try:
        assert ok is True
        held = locking.busy_locks()
        assert [os.path.basename(p) for p, _hint in held] == ["box.lock"]
    finally:
        lock.release()


def test_drop_planted_reports_failure_for_a_full_directory(locks_dir):
    dir_fd = os.open(locks_dir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.mkdir(os.path.join(locks_dir, "full.lock"))
        open(os.path.join(locks_dir, "full.lock", "x"), "w").close()
        assert locking._drop_planted(dir_fd, "full.lock") is False
        assert locking._drop_planted(dir_fd, "missing.lock") is True
    finally:
        os.close(dir_fd)


def test_is_planted_recognises_every_spelling():
    for code in (errno.ELOOP, errno.ENOTDIR, errno.EISDIR,
                 errno.EINVAL, errno.ENXIO):
        assert locking._is_planted(OSError(code, "x")) is True
    assert locking._is_planted(OSError(errno.EROFS, "x")) is False
