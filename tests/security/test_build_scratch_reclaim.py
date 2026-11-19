# Containment tests for the sweep of build scratch roots left behind.
#
# A build that is killed outright, or interrupted twice while it cleans
# up, leaves its scratch root -- every stage's rootfs -- in
# RUNTIME_DIR/build-tmp, and the next build removes it. That removal is
# the risky part: build-tmp is guest-writable on Termux (the runtime tree
# is under the $TERMUX_PREFIX bound read-write into every non-isolated
# container), so what it finds there may have been planted, and a root
# that looks abandoned may belong to a build that is still running.

import os
import subprocess
import sys

import pytest

from proot_distro.constants import RUNTIME_DIR
from proot_distro.operations.build import (
    _make_build_tmp,
    _reclaim_build_tmp,
)

BUILD_TMP = os.path.join(RUNTIME_DIR, "build-tmp")


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    (d / "keepsake").write_text("host content\n")
    return d


@pytest.fixture
def dead_pid():
    """The ID of a process that has exited and been reaped."""
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait()
    return child.pid


@pytest.fixture
def build_tmp():
    os.makedirs(BUILD_TMP, exist_ok=True)
    return BUILD_TMP


def _root(build_tmp, pid, suffix="0badc0de"):
    path = os.path.join(build_tmp, f"pd-build-{pid}.{suffix}")
    os.makedirs(os.path.join(path, "stage-0", "rootfs", "etc"))
    with open(os.path.join(path, "stage-0", "rootfs", "etc", "hostname"),
              "w") as f:
        f.write("left behind\n")
    return path


def _locker(path):
    """A process holding *path*'s scratch lock until its stdin closes."""
    child = subprocess.Popen(
        [sys.executable, "-c",
         "import fcntl, os, sys\n"
         "fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)\n"
         "fcntl.flock(fd, fcntl.LOCK_EX)\n"
         "print('ready', flush=True)\n"
         "sys.stdin.read()\n",
         path],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
    )
    assert child.stdout.readline() == b"ready\n"
    return child


def _release(child):
    child.stdin.close()
    child.wait()
    child.stdout.close()


def test_a_root_whose_build_is_gone_is_removed(build_tmp, dead_pid):
    _root(build_tmp, dead_pid)
    announced = []

    assert _reclaim_build_tmp(announced.append) == 1

    assert announced == [1]
    assert os.listdir(build_tmp) == []


def test_a_planted_link_is_not_followed(build_tmp, dead_pid, outside):
    os.symlink(str(outside),
               os.path.join(build_tmp, f"pd-build-{dead_pid}.0badc0de"))

    _reclaim_build_tmp()

    assert sorted(os.listdir(str(outside))) == ["keepsake"]


def test_a_link_inside_a_stale_root_is_unlinked_not_followed(
        build_tmp, dead_pid, outside):
    root = _root(build_tmp, dead_pid)
    os.symlink(str(outside), os.path.join(root, "stage-0", "rootfs", "mnt"))

    assert _reclaim_build_tmp() == 1

    assert os.listdir(build_tmp) == []
    assert sorted(os.listdir(str(outside))) == ["keepsake"]
    assert (outside / "keepsake").read_text() == "host content\n"


def test_a_planted_link_to_build_tmp_itself_is_refused(outside):
    # The trust walk refuses a planted `build-tmp`; nothing is swept
    # through it.
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    os.symlink(str(outside), BUILD_TMP)
    stale = outside / "pd-build-1.0badc0de"
    stale.mkdir()

    assert _reclaim_build_tmp() == 0

    assert stale.is_dir()


def test_a_running_build_is_kept_even_under_a_dead_process_id(
        build_tmp, dead_pid):
    # A live root renamed to a dead process's ID -- or one whose process
    # cannot be seen from here -- still holds its lock.
    root = _root(build_tmp, dead_pid)
    holder = _locker(root)
    try:
        assert _reclaim_build_tmp() == 0
        assert os.path.isdir(root)
    finally:
        _release(holder)

    assert _reclaim_build_tmp() == 1


def test_a_root_whose_process_is_alive_is_kept_unlocked(build_tmp):
    # A build of a version that took no lock, still running.
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
    )
    try:
        root = _root(build_tmp, child.pid)
        assert _reclaim_build_tmp() == 0
        assert os.path.isdir(root)
    finally:
        child.stdin.close()
        child.wait()


def test_a_root_just_made_is_kept_before_and_after_its_lock(build_tmp):
    root, fd = _make_build_tmp()
    try:
        # Named after this process, which is alive, and locked.
        assert os.path.basename(root).startswith(f"pd-build-{os.getpid()}.")
        assert _reclaim_build_tmp() == 0
        assert os.path.isdir(root)
    finally:
        os.close(fd)


def test_the_lock_is_held_by_the_root_descriptor(build_tmp):
    root, fd = _make_build_tmp()
    try:
        probe = subprocess.run(
            [sys.executable, "-c",
             "import fcntl, os, sys\n"
             "fd = os.open(sys.argv[1], os.O_RDONLY | os.O_DIRECTORY)\n"
             "try:\n"
             "    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)\n"
             "except BlockingIOError:\n"
             "    print('held')\n"
             "else:\n"
             "    print('free')\n",
             root],
            capture_output=True, text=True, check=True,
        )
        assert probe.stdout == "held\n"
    finally:
        os.close(fd)


@pytest.mark.parametrize("name", [
    "pd-build-{pid}",
    "pd-build-{pid}.0badc0d",
    "pd-build-{pid}.0BADC0DE",
    "pd-build-{pid}.0badc0de.moved",
    "pd-build-x.0badc0de",
    "something-else",
])
def test_names_a_build_does_not_give_are_left_alone(
        build_tmp, dead_pid, name):
    path = os.path.join(build_tmp, name.format(pid=dead_pid))
    os.mkdir(path)

    assert _reclaim_build_tmp() == 0

    assert os.path.isdir(path)


def test_a_file_under_a_scratch_name_is_left_alone(build_tmp, dead_pid):
    path = os.path.join(build_tmp, f"pd-build-{dead_pid}.0badc0de")
    with open(path, "w") as f:
        f.write("not a directory\n")

    assert _reclaim_build_tmp() == 0

    assert os.path.isfile(path)
