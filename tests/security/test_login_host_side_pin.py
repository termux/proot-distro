# Containment tests for the work `login` does on the *host* side of proot,
# between pinning the rootfs and exec'ing.
#
# The rootfs proot receives is pinned (see test_login_rootfs_pin), but
# every other step still named it: guestfile opened the rootfs by path
# before walking it, so the guest's passwd, group and shell were read
# from whatever `containers/<name>` led to; the profile.d snippet was
# written there; and sysdata/ and shm/ were created — and chmod'ed 0700
# and 1777 — beside it. `login` takes only a shared lock, so a live
# session was free to move the directory aside and leave a symlink under
# the name after the installed check had walked it.
#
# The swap is staged deterministically here, on the first step that runs
# after the pin, rather than raced for: that is the state the loser of
# the race is in.

import os
import shutil
import stat
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands import login as login_mod
from proot_distro.commands.login import command_login
from proot_distro.commands.login.env import inject_termux_profile
from proot_distro.commands.login.passwd import read_passwd_entry
from proot_distro.guestfile import read_guest_file
from proot_distro.paths import (
    container_dir, container_rootfs, open_container_pair,
)
from proot_distro.shm import make_shm_dir
from proot_distro.sysdata import setup_fake_sysdata


HOST_ARCH = get_device_cpu_arch()

REAL_PASSWD = "root:x:4242:4242:root:/realhome:/bin/REALSH\n"
DECOY_PASSWD = "root:x:9999:9999:root:/decoyhome:/bin/DECOYSH\n"


@pytest.fixture
def swappable(builders):
    """An installed container plus a stand-in, and the move between them.

    Both are complete containers, so login gets as far as the exec
    either way and the only difference is which one it read from.
    """
    builders.make_container("box", arch=HOST_ARCH)
    real = container_rootfs("box")
    with open(os.path.join(real, "etc", "passwd"), "w") as fh:
        fh.write(REAL_PASSWD)
    os.makedirs(os.path.join(real, "bin"), exist_ok=True)
    with open(os.path.join(real, "bin", "REALSH"), "w") as fh:
        fh.write("#!/bin/sh\n")
    os.makedirs(os.path.join(real, "etc", "profile.d"), exist_ok=True)

    decoy = os.path.join(os.path.dirname(container_dir("box")), "decoy")
    shutil.copytree(container_dir("box"), decoy)
    with open(os.path.join(decoy, "rootfs", "etc", "passwd"), "w") as fh:
        fh.write(DECOY_PASSWD)
    os.makedirs(os.path.join(decoy, "rootfs", "bin"), exist_ok=True)
    with open(os.path.join(decoy, "rootfs", "bin", "DECOYSH"), "w") as fh:
        fh.write("#!/bin/sh\n")
    os.makedirs(os.path.join(decoy, "rootfs", "etc", "profile.d"),
                exist_ok=True)

    def swap():
        target = container_dir("box")
        os.rename(target, target + ".moved")
        os.symlink(decoy, target)

    # After the swap the real tree answers to `<name>.moved`; the name it
    # was pinned under leads to the stand-in. Assertions use `moved`, so
    # they are about the inode that was pinned rather than about a name.
    return SimpleNamespace(real=real, decoy=decoy, swap=swap,
                           moved=container_dir("box") + ".moved")


def _login_args(name, **over):
    base = dict(container_name=name, get_proot_cmd=True, user="root",
                kernel=None, hostname="localhost", work_dir="",
                redirect_ports=False, isolated=False, minimal=False,
                shared_home=False, shared_tmp=False, shared_x11=False,
                no_link2symlink=False, no_sysvipc=False,
                no_kill_on_exit=False, detach=False,
                bind=[], env=[], login_cmd=[], emulator=None)
    base.update(over)
    return SimpleNamespace(**base)


# --- the user lookup, end to end -------------------------------------------

def test_passwd_is_read_from_the_pinned_rootfs_after_a_swap(
        swappable, capsys, monkeypatch):
    """uid, gid, home and shell all come from the container that was pinned.

    These are the values --change-id runs the session as and the binary
    proot is told to execute, so reading them out of a directory someone
    else chose decides the whole session.
    """
    real_detect = login_mod._detect_dist_type

    def detect_then_swap(rootfs, rootfs_fd=None):
        answer = real_detect(rootfs, rootfs_fd)
        swappable.swap()
        return answer

    monkeypatch.setattr(login_mod, "_detect_dist_type", detect_then_swap)

    with pytest.raises(SystemExit) as exc:
        command_login(_login_args("box"))
    assert exc.value.code == 0
    out = capsys.readouterr().out

    assert "--change-id=4242:4242" in out
    assert "HOME=/realhome" in out
    assert "/bin/REALSH" in out
    assert "9999" not in out
    assert "DECOY" not in out


def test_dist_type_is_decided_from_the_pinned_rootfs(swappable, capsys,
                                                     monkeypatch):
    """The termux/normal answer shapes the whole session; pin it too."""
    from proot_distro.constants import TERMUX_PREFIX

    # Only the stand-in looks like a Termux container.
    marker = os.path.join(swappable.decoy, "rootfs",
                          TERMUX_PREFIX.lstrip("/"), "bin")
    os.makedirs(marker, exist_ok=True)
    with open(os.path.join(marker, "login"), "w") as fh:
        fh.write("#!/bin/sh\n")

    real_pair = login_mod.open_container_pair

    def pair_then_swap(name, **kw):
        fds = real_pair(name, **kw)
        swappable.swap()
        return fds

    monkeypatch.setattr(login_mod, "open_container_pair", pair_then_swap)

    with pytest.raises(SystemExit):
        command_login(_login_args("box"))
    out = capsys.readouterr().out
    # A termux-type session gets no --change-id at all, so its presence
    # is the proof the real (normal-type) container decided this.
    assert "--change-id=" in out


# --- the pieces, directly --------------------------------------------------

def test_guest_reads_ignore_a_swapped_container_name(swappable):
    container_fd, rootfs_fd = open_container_pair("box")
    try:
        swappable.swap()
        assert read_guest_file(container_rootfs("box"), "/etc/passwd",
                               root_fd=rootfs_fd) == REAL_PASSWD
        # ...and by name, which is what the pin replaces.
        assert read_guest_file(container_rootfs("box"),
                               "/etc/passwd") == DECOY_PASSWD
        assert read_passwd_entry(container_rootfs("box"), "root",
                                 root_fd=rootfs_fd)[2] == "4242"
    finally:
        os.close(rootfs_fd)
        os.close(container_fd)


def test_profile_snippet_lands_in_the_pinned_rootfs(swappable):
    container_fd, rootfs_fd = open_container_pair("box")
    try:
        swappable.swap()
        inject_termux_profile(container_rootfs("box"), {"FOO": "bar"},
                              rootfs_fd=rootfs_fd)
    finally:
        os.close(rootfs_fd)
        os.close(container_fd)

    written = os.path.join(swappable.moved, "rootfs", "etc", "profile.d",
                           "termux-profile.sh")
    assert os.path.isfile(written)
    assert not os.path.exists(os.path.join(
        swappable.decoy, "rootfs", "etc", "profile.d", "termux-profile.sh"))


def test_sysdata_is_written_under_the_pinned_container_dir(swappable):
    container_fd, rootfs_fd = open_container_pair("box")
    try:
        swappable.swap()
        setup_fake_sysdata(container_rootfs("box"), container_fd=container_fd)
    finally:
        os.close(rootfs_fd)
        os.close(container_fd)

    real_dir = os.path.join(swappable.moved, "sysdata")
    assert os.path.isdir(real_dir)
    assert os.listdir(real_dir)
    assert not os.path.exists(os.path.join(swappable.decoy, "sysdata"))


def test_shm_store_is_made_under_the_pinned_container_dir(swappable):
    container_fd, rootfs_fd = open_container_pair("box")
    try:
        swappable.swap()
        path = make_shm_dir(container_rootfs("box"),
                            container_fd=container_fd)
    finally:
        os.close(rootfs_fd)
        os.close(container_fd)

    assert path is not None
    real_dir = os.path.join(swappable.moved, "shm")
    assert os.path.isdir(real_dir)
    # 1777 went onto the directory the walk validated, not onto a name.
    assert stat.S_IMODE(os.stat(real_dir).st_mode) == 0o1777
    assert not os.path.exists(os.path.join(swappable.decoy, "shm"))


# --- the pair itself -------------------------------------------------------

def test_pair_descends_rather_than_walking_twice(builders):
    """The rootfs comes off the container directory's own descriptor."""
    builders.make_container("box", arch=HOST_ARCH)
    container_fd, rootfs_fd = open_container_pair("box")
    try:
        want = os.stat(container_rootfs("box"))
        got = os.fstat(rootfs_fd)
        assert (got.st_dev, got.st_ino) == (want.st_dev, want.st_ino)
        parent = os.stat(container_dir("box"))
        held = os.fstat(container_fd)
        assert (held.st_dev, held.st_ino) == (parent.st_dev, parent.st_ino)
    finally:
        os.close(rootfs_fd)
        os.close(container_fd)


def test_pair_reports_a_missing_container_as_not_found():
    with pytest.raises(FileNotFoundError):
        open_container_pair("nope")


def test_pair_reports_a_missing_rootfs_as_not_found(builders):
    os.makedirs(container_dir("halfway"))
    with pytest.raises(FileNotFoundError):
        open_container_pair("halfway")


def test_pair_refuses_a_planted_rootfs(builders, tmp_path):
    """A rootfs that is not a plain directory stops the command."""
    os.makedirs(container_dir("planted"))
    outside = tmp_path / "outside"
    outside.mkdir()
    os.symlink(str(outside), container_rootfs("planted"))
    with pytest.raises(SystemExit) as exc:
        open_container_pair("planted")
    assert exc.value.code == 1
