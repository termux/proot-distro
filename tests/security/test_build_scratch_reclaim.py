# Containment tests for the sweep of build scratch roots left behind.
#
# A build that is killed outright, or interrupted twice while it cleans
# up, leaves its scratch root -- every stage's rootfs -- in
# RUNTIME_DIR/build-tmp, or in the system temporary directory when the
# runtime tree could not hold one. The next build and `clear-cache`
# remove it. That removal is the risky part: build-tmp is guest-writable
# on Termux (the runtime tree is under the $TERMUX_PREFIX bound
# read-write into every non-isolated container), and so is the temporary
# directory under $PREFIX, so what the sweep finds may have been planted.
# A root that looks abandoned may belong to a build still running, and in
# a shared /tmp it may belong to another user.

import os
import subprocess
import sys
import tempfile

import pytest

from proot_distro import build_scratch
from proot_distro.build_scratch import (
    BUILD_TMP,
    make_scratch_root,
    reclaim_scratch_roots,
)


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
def live_child():
    """A running process that has nothing to do with any build."""
    child = subprocess.Popen(
        [sys.executable, "-c", "import sys; sys.stdin.read()"],
        stdin=subprocess.PIPE,
    )
    yield child
    child.stdin.close()
    child.wait()


@pytest.fixture
def build_tmp():
    os.makedirs(BUILD_TMP, exist_ok=True)
    return BUILD_TMP


@pytest.fixture
def system_tmp(tmp_path, monkeypatch):
    """A system temporary directory of this test's own."""
    path = tmp_path / "system-tmp"
    path.mkdir()
    monkeypatch.setattr(tempfile, "tempdir", str(path))
    return str(path)


@pytest.fixture(params=["build-tmp", "system-tmp"])
def parent(request, build_tmp, system_tmp):
    """Each place a build makes its root, and so the sweep looks."""
    return build_tmp if request.param == "build-tmp" else system_tmp


def _name(pid, start=None, suffix="0badc0de"):
    stamp = "" if start is None else f"-{start}"
    return f"pd-build-{pid}{stamp}.{suffix}"


def _root(parent, pid, start=None, suffix="0badc0de"):
    path = os.path.join(parent, _name(pid, start, suffix))
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


def _lock_state(path):
    """'held' or 'free': what another process finds *path*'s lock to be."""
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
         path],
        capture_output=True, text=True, check=True,
    )
    return probe.stdout.strip()


@pytest.mark.parametrize("start", [None, 4242])
def test_a_root_whose_build_is_gone_is_removed(parent, dead_pid, start):
    _root(parent, dead_pid, start)
    announced = []

    assert reclaim_scratch_roots(announced.append) == 1

    assert announced == [1]
    assert os.listdir(parent) == []


def test_roots_in_both_places_are_announced_together(
        build_tmp, system_tmp, dead_pid):
    _root(build_tmp, dead_pid)
    _root(system_tmp, dead_pid, 4242)
    announced = []

    assert reclaim_scratch_roots(announced.append) == 2

    assert announced == [2]
    assert os.listdir(build_tmp) == [] and os.listdir(system_tmp) == []


def test_a_root_whose_process_id_was_reused_is_removed(parent, live_child):
    """Regression: a reused ID kept a dead build's root indefinitely.

    The ID belongs to a running process again, but not to the one that
    made the root: that one started at another time.
    """
    start = build_scratch._process_start(live_child.pid)
    assert start is not None
    _root(parent, live_child.pid, start + 1)

    assert reclaim_scratch_roots() == 1

    assert os.listdir(parent) == []


def test_a_root_named_after_another_users_process_is_removed(parent):
    # This user may signal every process running as itself, so a process
    # it may not signal did not make a root it owns.
    try:
        os.kill(1, 0)
    except PermissionError:
        pass
    else:
        pytest.skip("process 1 runs as this user here")
    _root(parent, 1)
    _root(parent, 1, 4242, suffix="1badc0de")

    assert reclaim_scratch_roots() == 2

    assert os.listdir(parent) == []


def test_another_users_root_is_left_alone(parent, dead_pid, monkeypatch):
    root = _root(parent, dead_pid, 4242)
    monkeypatch.setattr(build_scratch.os, "geteuid",
                        lambda uid=os.geteuid(): uid + 1)

    assert reclaim_scratch_roots() == 0

    assert os.path.isdir(root)


def test_a_planted_link_is_not_followed(parent, dead_pid, outside):
    os.symlink(str(outside), os.path.join(parent, _name(dead_pid)))

    reclaim_scratch_roots()

    assert sorted(os.listdir(str(outside))) == ["keepsake"]


def test_a_link_inside_a_stale_root_is_unlinked_not_followed(
        parent, dead_pid, outside):
    root = _root(parent, dead_pid, 4242)
    os.symlink(str(outside), os.path.join(root, "stage-0", "rootfs", "mnt"))

    assert reclaim_scratch_roots() == 1

    assert os.listdir(parent) == []
    assert sorted(os.listdir(str(outside))) == ["keepsake"]
    assert (outside / "keepsake").read_text() == "host content\n"


def test_a_planted_link_to_build_tmp_itself_is_refused(system_tmp, outside):
    # The trust walk refuses a planted `build-tmp`; nothing is swept
    # through it.
    os.makedirs(os.path.dirname(BUILD_TMP), exist_ok=True)
    os.symlink(str(outside), BUILD_TMP)
    stale = outside / "pd-build-1.0badc0de"
    stale.mkdir()

    assert reclaim_scratch_roots() == 0

    assert stale.is_dir()


def test_a_running_build_is_kept_even_under_a_dead_process_id(
        parent, dead_pid):
    # A live root renamed to a dead process's ID -- or one whose process
    # cannot be seen from here -- still holds its lock.
    root = _root(parent, dead_pid, 4242)
    holder = _locker(root)
    try:
        assert reclaim_scratch_roots() == 0
        assert os.path.isdir(root)
    finally:
        _release(holder)

    assert reclaim_scratch_roots() == 1


def test_a_root_whose_process_is_alive_is_kept_unlocked(parent, live_child):
    # Unlocked: a 5.10 build, which took no lock, or one on a filesystem
    # that cannot hold one. The process that made it is still running.
    old = _root(parent, live_child.pid)
    new = _root(parent, live_child.pid,
                build_scratch._process_start(live_child.pid),
                suffix="1badc0de")

    assert reclaim_scratch_roots() == 0

    assert os.path.isdir(old) and os.path.isdir(new)


def test_a_root_just_made_is_kept_before_and_after_its_lock(build_tmp):
    root, fd = make_scratch_root()
    try:
        # Named after this process, which is alive, and locked.
        start = build_scratch._process_start(os.getpid())
        assert os.path.basename(root).startswith(
            f"pd-build-{os.getpid()}-{start}.")
        assert reclaim_scratch_roots() == 0
        assert os.path.isdir(root)
    finally:
        os.close(fd)


def test_the_lock_is_held_by_the_root_descriptor(build_tmp):
    root, fd = make_scratch_root()
    try:
        assert _lock_state(root) == "held"
    finally:
        os.close(fd)


def test_a_fallback_root_is_made_like_any_other(system_tmp, outside):
    """Regression: a root in the system temp dir carried no process ID.

    mkdtemp() named it pd-build- and eight random characters, unlocked,
    so nothing could tell a dead build's from a running one's and no
    sweep ever removed one.
    """
    os.makedirs(os.path.dirname(BUILD_TMP), exist_ok=True)
    os.symlink(str(outside), BUILD_TMP)
    root, fd = make_scratch_root()
    try:
        assert os.path.dirname(root) == system_tmp
        start = build_scratch._process_start(os.getpid())
        assert os.path.basename(root).startswith(
            f"pd-build-{os.getpid()}-{start}.")
        assert _lock_state(root) == "held"
        assert reclaim_scratch_roots() == 0
        assert sorted(os.listdir(str(outside))) == ["keepsake"]
    finally:
        os.close(fd)
        os.rmdir(root)


@pytest.mark.parametrize("name", [
    "pd-build-{pid}",
    "pd-build-{pid}.0badc0d",
    "pd-build-{pid}.0BADC0DE",
    "pd-build-{pid}.0badc0de.moved",
    "pd-build-{pid}-.0badc0de",
    "pd-build-{pid}-x.0badc0de",
    "pd-build-x.0badc0de",
    # What 5.10 made in the system temp dir: no process to ask about.
    "pd-build-k3_9zq0a",
    "something-else",
])
def test_names_a_build_does_not_give_are_left_alone(
        parent, dead_pid, name):
    path = os.path.join(parent, name.format(pid=dead_pid))
    os.mkdir(path)

    assert reclaim_scratch_roots() == 0

    assert os.path.isdir(path)


def test_a_file_under_a_scratch_name_is_left_alone(parent, dead_pid):
    path = os.path.join(parent, _name(dead_pid, 4242))
    with open(path, "w") as f:
        f.write("not a directory\n")

    assert reclaim_scratch_roots() == 0

    assert os.path.isfile(path)


@pytest.mark.parametrize("stat_line", [
    # pid (comm) state, then fields 4 to 21, then 22: the start time.
    b"1234 (a) b) c) S " + b"0 " * 18 + b"777 99 0\n",
    b"1234 (plain) S " + b"0 " * 18 + b"777 99 0\n",
])
def test_the_start_time_is_read_after_the_last_parenthesis(
        monkeypatch, tmp_path, stat_line):
    # A command name may hold ") " itself; field 22 is counted from the
    # parenthesis that closes it.
    stat_file = tmp_path / "stat"
    stat_file.write_bytes(stat_line)
    real_open = open

    def fake_open(path, *args, **kwargs):
        if path == "/proc/1234/stat":
            return real_open(stat_file, *args, **kwargs)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", fake_open)

    assert build_scratch._process_start(1234) == 777
