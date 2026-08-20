# Containment tests for proot_distro.guestfile — reading a file out of a
# container the way the guest sees it.
#
# `login` takes a user's uid, gid, home and shell out of the container's
# /etc/passwd and /etc/group before it exec's proot, and the build engine
# resolves USER and COPY --chown against a stage rootfs's copies. The file
# and every directory component leading to it are image or guest content,
# so composing `<rootfs><guest path>` and handing the string to open() let
# the *host* kernel resolve the middle of it: an image shipping
# `etc -> /etc` had login read the host's passwd file and hand a host
# user's identity to the session. A FIFO under either name blocked the
# command for as long as no peer turned up, and a file with no newline in
# it was read into memory whole.

import os
import resource
import stat

import pytest

from proot_distro import dirfd, guestfile
from proot_distro.commands.login.passwd import (
    find_passwd_by_uid, passwd_available, read_group_gid, read_passwd_entry,
    shell_available,
)


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "rootfs"
    (root / "etc").mkdir(parents=True)
    (root / "etc" / "passwd").write_text("root:x:0:0::/root:/bin/sh\n")
    (root / "etc" / "group").write_text("root:x:0:\n")
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "passwd").write_text(
        "hostuser:x:4242:4242:host:/host/home:/host/shell\n"
    )
    (outside / "group").write_text("hostgroup:x:4242:\n")
    return str(root), outside


# --- the path a component can take ----------------------------------------

def test_absolute_component_symlink_does_not_reach_the_host(env):
    root, outside = env
    os.rename(os.path.join(root, "etc"), os.path.join(root, "realetc"))
    os.symlink(str(outside), os.path.join(root, "etc"))

    assert guestfile.read_guest_file(root, "/etc/passwd") is None
    assert not passwd_available(root)
    assert read_passwd_entry(root, "hostuser") == []
    assert find_passwd_by_uid(root, "4242") == ("", "", "")
    assert read_group_gid(root, "hostgroup") == ""


def test_the_file_itself_as_an_escaping_symlink_is_refused(env):
    root, outside = env
    os.unlink(os.path.join(root, "etc", "passwd"))
    os.symlink(str(outside / "passwd"), os.path.join(root, "etc", "passwd"))

    assert guestfile.read_guest_file(root, "/etc/passwd") is None


def test_guest_absolute_symlink_is_re_rooted(env):
    # A Nix-style image: /etc/passwd points at an absolute path that only
    # exists inside the guest. That has to keep working.
    root, _outside = env
    os.makedirs(os.path.join(root, "nix", "store"))
    with open(os.path.join(root, "nix", "store", "passwd"), "w") as fh:
        fh.write("nixuser:x:7:7::/home/nix:/bin/nixsh\n")
    os.unlink(os.path.join(root, "etc", "passwd"))
    os.symlink("/nix/store/passwd", os.path.join(root, "etc", "passwd"))

    assert read_passwd_entry(root, "nixuser")[2] == "7"


def test_relative_symlink_follows_from_the_link(env):
    root, _outside = env
    os.rename(os.path.join(root, "etc"), os.path.join(root, "realetc"))
    os.symlink("realetc", os.path.join(root, "etc"))

    assert read_passwd_entry(root, "root")[2] == "0"


def test_dotdot_clamps_at_the_rootfs(env):
    root, _outside = env
    assert guestfile.read_guest_file(root, "/../outside/passwd") is None
    assert guestfile.read_guest_file(
        root, "/etc/../../../etc/passwd"
    ) == "root:x:0:0::/root:/bin/sh\n"


def test_dotdot_refuses_a_level_that_moved_underneath_the_walk(env,
                                                              monkeypatch):
    # ".." is the one descriptor not derived from a name under a directory
    # already validated, so it is checked against the level the walk came
    # down from. Move that level while the walk is between the two and it
    # would otherwise carry on somewhere else entirely.
    root, outside = env
    os.makedirs(os.path.join(root, "a", "b"))
    (outside / "b").mkdir()
    (outside / "passwd").rename(outside / "b" / "passwd")

    real_opendir_at = dirfd.opendir_at
    moved = []

    def moving_opendir_at(fd, name):
        if name == os.pardir and not moved:
            moved.append(True)
            os.rename(os.path.join(root, "a", "b"),
                      str(outside / "b" / "moved"))
        return real_opendir_at(fd, name)

    monkeypatch.setattr(guestfile.dirfd, "opendir_at", moving_opendir_at)
    assert guestfile.read_guest_file(root, "/a/b/../passwd") is None
    assert moved


# --- what may be read ------------------------------------------------------

def test_a_fifo_is_neither_opened_nor_reported_as_a_file(env):
    root, _outside = env
    os.unlink(os.path.join(root, "etc", "passwd"))
    os.mkfifo(os.path.join(root, "etc", "passwd"))

    # Both answers come from an lstat, so neither blocks on a peer that
    # never arrives; the test finishing at all is the assertion.
    assert not passwd_available(root)
    assert guestfile.read_guest_file(root, "/etc/passwd") is None


def test_a_directory_is_not_a_file(env):
    root, _outside = env
    assert not guestfile.guest_file_exists(root, "/etc")
    assert guestfile.read_guest_file(root, "/etc") is None


def test_an_oversized_file_is_capped(env):
    root, _outside = env
    with open(os.path.join(root, "etc", "passwd"), "w") as fh:
        fh.write("root:x:0:0::/root:/bin/sh\n")
        fh.write("filler:x:1:1::/f:/f\n" * 200000)
    data = guestfile.read_guest_file(root, "/etc/passwd")
    assert 0 < len(data) <= guestfile.MAX_ID_FILE_BYTES
    assert read_passwd_entry(root, "root")[2] == "0"


def test_a_symlink_loop_ends(env):
    root, _outside = env
    os.unlink(os.path.join(root, "etc", "passwd"))
    os.symlink("passwd2", os.path.join(root, "etc", "passwd"))
    os.symlink("passwd", os.path.join(root, "etc", "passwd2"))

    assert guestfile.read_guest_file(root, "/etc/passwd") is None


def test_an_execute_only_shell_still_counts_as_present(env):
    # The existence question is answered from the walk's lstat, so a shell
    # the image ships unreadable is still a shell.
    root, _outside = env
    os.makedirs(os.path.join(root, "bin"))
    shell = os.path.join(root, "bin", "sh")
    with open(shell, "w") as fh:
        fh.write("#!/bin/sh\n")
    os.chmod(shell, 0o111)

    assert shell_available(root, "/bin/sh")


def test_the_shell_check_cannot_answer_about_a_host_file(env):
    root, outside = env
    victim = outside / "victim-shell"
    victim.write_text("#!/bin/sh\n")
    os.symlink(str(victim), os.path.join(root, "escape"))

    assert not shell_available(root, "/escape")
    assert not shell_available(root, "/../outside/victim-shell")


# --- proot's hard-link stand-ins ------------------------------------------

def _make_l2s(root, name, content):
    """An entry proot's --link2symlink extension would have left behind."""
    store = os.path.join(root, ".l2s")
    os.makedirs(store, exist_ok=True)
    backing = os.path.join(store, f".proot.l2s.{name}0001.0001")
    with open(backing, "w") as fh:
        fh.write(content)
    intermediate = os.path.join(store, f".proot.l2s.{name}0001")
    os.symlink(backing, intermediate)
    return intermediate


def test_an_l2s_stand_in_is_followed_to_its_backing_file(env):
    root, _outside = env
    intermediate = _make_l2s(root, "passwd", "l2s:x:9:9::/l:/bin/l\n")
    os.unlink(os.path.join(root, "etc", "passwd"))
    os.symlink(intermediate, os.path.join(root, "etc", "passwd"))

    assert read_passwd_entry(root, "l2s")[2] == "9"


def test_an_l2s_chain_leaving_the_rootfs_is_refused(env):
    root, outside = env
    victim = outside / "secret"
    victim.write_text("hostuser:x:4242:4242::/h:/hs\n")
    store = os.path.join(root, ".l2s")
    os.makedirs(store)
    intermediate = os.path.join(store, ".proot.l2s.passwd0001")
    os.symlink(str(victim), intermediate)
    os.unlink(os.path.join(root, "etc", "passwd"))
    os.symlink(intermediate, os.path.join(root, "etc", "passwd"))

    assert guestfile.read_guest_file(root, "/etc/passwd") is None


# --- descriptors -----------------------------------------------------------

def _open_fds():
    return set(os.listdir("/proc/self/fd"))


def test_a_deep_path_does_not_hold_a_descriptor_per_level(env):
    # How many components a symlink target names is the image's choice.
    # One descriptor per level made that decide how many this process
    # holds, so a crafted /etc/passwd could exhaust the whole table.
    root, _outside = env
    fd = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
    try:
        for _ in range(600):
            os.mkdir("d", dir_fd=fd)
            nxt = os.open("d", os.O_RDONLY | os.O_DIRECTORY, dir_fd=fd)
            os.close(fd)
            fd = nxt
        with open("passwd", "w",
                  opener=lambda p, f: os.open(p, f, 0o644, dir_fd=fd)) as fh:
            fh.write("deep:x:5:5::/d:/bin/d\n")
    finally:
        os.close(fd)

    deep = "/" + "/".join(["d"] * 600) + "/passwd"
    soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
    before = _open_fds()
    resource.setrlimit(resource.RLIMIT_NOFILE, (64, hard))
    try:
        assert (guestfile.read_guest_file(root, deep)
                == "deep:x:5:5::/d:/bin/d\n")
    finally:
        resource.setrlimit(resource.RLIMIT_NOFILE, (soft, hard))
    assert len(_open_fds() - before) == 0

    # pytest's own tmp-dir reaper recurses; this one is deeper than the
    # interpreter's limit is tall.
    dirfd.remove_tree(os.path.join(root, "d"))


def test_no_descriptor_is_left_behind_by_a_refused_lookup(env):
    root, outside = env
    os.symlink(str(outside), os.path.join(root, "escape"))
    before = _open_fds()
    for _ in range(20):
        guestfile.read_guest_file(root, "/escape/passwd")
        guestfile.read_guest_file(root, "/etc/passwd")
        guestfile.read_guest_file(root, "/nowhere/at/all")
        guestfile.guest_file_exists(root, "/etc")
    assert len(_open_fds() - before) == 0


def test_a_missing_rootfs_is_simply_absent(tmp_path):
    missing = str(tmp_path / "gone")
    assert guestfile.read_guest_file(missing, "/etc/passwd") is None
    assert not guestfile.guest_file_exists(missing, "/etc/passwd")


def test_stat_flags_are_the_entrys_own(env):
    # A guard on the walk's promise that what it hands back is never a
    # symlink's stat: every caller decides on S_ISREG.
    root, _outside = env
    os.makedirs(os.path.join(root, "real"))
    with open(os.path.join(root, "real", "f"), "w") as fh:
        fh.write("x")
    os.symlink("/real/f", os.path.join(root, "link"))

    found = guestfile._resolve(root, "/link")
    assert found is not None
    stack, name, st = found
    try:
        assert name == "f"
        assert stat.S_ISREG(st.st_mode)
    finally:
        for fd in stack:
            os.close(fd)


def test_a_refused_component_is_not_a_readable_file(env):
    # dirfd reports O_NOFOLLOW|O_DIRECTORY on a symlink as ENOTDIR rather
    # than ELOOP; either errno means "no file", never the target.
    root, outside = env
    os.symlink(str(outside), os.path.join(root, "etc2"))
    assert guestfile.read_guest_file(root, "/etc2/passwd") is None
    assert not guestfile.guest_file_exists(root, "/etc2/passwd")
