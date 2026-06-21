# Tests for proot_distro.locking — focused on ContainerLock.disown(),
# the handover path used by detached (`--detach`) sessions.
#
# A detached session double-forks; the daemon inherits the foreground's
# (inheritable) lock fd, which refers to the SAME open file description.
# os.dup() reproduces that sharing in-process: a dup'd fd shares the
# flock exactly like a fork-inherited one, so we can assert the lock
# stays held after the original handle is released — provided release()
# skips LOCK_UN (disowned).

import fcntl
import os

from proot_distro import locking
from proot_distro.locking import ContainerLock


def _exclusive_lock_free(path) -> bool:
    """True iff an independent fd can take LOCK_EX on *path* right now."""
    fd = os.open(path, os.O_RDONLY)
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            return False
        fcntl.flock(fd, fcntl.LOCK_UN)
        return True
    finally:
        os.close(fd)


def test_normal_release_frees_lock_even_with_duplicate_fd():
    # Without disown, release() issues LOCK_UN, which frees the lock for
    # ALL duplicates of the open file description (per flock(2)).
    lock = ContainerLock("box", exclusive=True, command="test")
    assert lock.acquire() is True
    path = locking.container_lock_path("box")
    keeper = os.dup(lock._fd.fileno())  # shares the OFD, like a fork
    try:
        assert _exclusive_lock_free(path) is False  # held
        lock.release()                              # normal LOCK_UN
        assert _exclusive_lock_free(path) is True   # freed despite keeper
    finally:
        os.close(keeper)


def test_disowned_release_keeps_lock_until_duplicate_closes():
    # With disown, release() closes our fd WITHOUT LOCK_UN, so a duplicate
    # (the would-be daemon) keeps the lock held until it too closes.
    lock = ContainerLock("box", exclusive=True, command="test")
    assert lock.acquire() is True
    path = locking.container_lock_path("box")
    keeper = os.dup(lock._fd.fileno())  # stand-in for the forked daemon
    try:
        lock.disown()
        lock.release()
        assert _exclusive_lock_free(path) is False  # still held by keeper
    finally:
        os.close(keeper)
    # Once the last duplicate closes, the lock is gone.
    assert _exclusive_lock_free(path) is True


def test_disown_clears_held_exclusive_registry():
    # release() must still drop the re-entrancy bookkeeping so a later
    # acquire on the same name performs a real flock.
    lock = ContainerLock("box", exclusive=True, command="test")
    assert lock.acquire() is True
    path = locking.container_lock_path("box")
    assert path in locking._held_exclusive
    lock.disown()
    lock.release()
    assert path not in locking._held_exclusive
