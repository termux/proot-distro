# Containment tests for the rootfs `login` hands to proot.
#
# container_is_installed() walks containers/<name>/rootfs with O_NOFOLLOW,
# but the answer was thrown away and the *path* put into the argv, and
# proot resolves --rootfs by name long after every check here has run.
# That directory is guest-writable on Termux and `login` takes only a
# shared lock, so a live session could move it aside and leave a symlink
# under the name in between — and the next session started with a host
# directory of that session's choosing as its root. Verified against
# proot itself: with the swap staged, `--rootfs=<path>` starts in the
# planted directory and the pinned form starts in the real one.
#
# The argv now says "." and the process chdirs into the descriptor the
# walk validated, so proot canonicalises the guest root against getcwd()
# — the inode — rather than against a name.

import os
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands import login as login_mod
from proot_distro.commands.login import command_login
from proot_distro.paths import container_dir, container_rootfs


HOST_ARCH = get_device_cpu_arch()


def _login_args(name, **over):
    base = dict(container_name=name, get_proot_cmd=False, user="root",
                kernel=None, hostname="localhost", work_dir="",
                redirect_ports=False, isolated=False, minimal=False,
                shared_home=False, shared_tmp=False, shared_x11=False,
                no_link2symlink=False, no_sysvipc=False,
                no_kill_on_exit=False, detach=False,
                bind=[], env=[], login_cmd=[], emulator=None)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def captured_exec(monkeypatch):
    """Stand in for execvpe and record the argv and the cwd it would use."""
    seen = {}

    def fake_execvpe(binary, argv, env):
        seen["argv"] = list(argv)
        seen["cwd"] = os.stat(os.curdir)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_execvpe)
    here = os.open(os.curdir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        yield seen
    finally:
        # command_login chdirs into the container on its way to the exec.
        os.fchdir(here)
        os.close(here)


@pytest.fixture
def decoy(builders, tmp_path):
    """An installed container plus a full stand-in to redirect it to.

    The stand-in is a complete container directory, so login gets as far
    as the exec either way and the only difference is which rootfs it
    ends up in.
    """
    builders.make_container("box", arch=HOST_ARCH)
    real = container_rootfs("box")
    with open(os.path.join(real, "I_AM_REAL"), "w") as fh:
        fh.write("yes\n")

    builders.make_container("standin", arch=HOST_ARCH)
    other = container_dir("standin")

    def swap():
        target = container_dir("box")
        os.rename(target, target + ".moved")
        os.symlink(other, target)

    return swap


def test_argv_names_the_pinned_root_not_a_path(builders, captured_exec):
    builders.make_container("box", arch=HOST_ARCH)
    with pytest.raises(SystemExit):
        command_login(_login_args("box"))
    assert "--rootfs=." in captured_exec["argv"]


def test_session_starts_in_the_pinned_rootfs_after_a_swap(
        decoy, captured_exec, monkeypatch):
    """Swapped after the pin: proot still gets the real directory."""
    real_st = os.stat(container_rootfs("box"))
    real_detect = login_mod._detect_dist_type

    def detect_then_swap(rootfs, rootfs_fd=None):
        answer = real_detect(rootfs, rootfs_fd)
        decoy()
        return answer

    monkeypatch.setattr(login_mod, "_detect_dist_type", detect_then_swap)

    with pytest.raises(SystemExit):
        command_login(_login_args("box"))

    # "." is what proot resolves, and it is the inode the walk validated —
    # not whatever containers/box leads to now.
    assert "--rootfs=." in captured_exec["argv"]
    cwd = captured_exec["cwd"]
    assert (cwd.st_dev, cwd.st_ino) == (real_st.st_dev, real_st.st_ino)


def test_get_proot_cmd_still_prints_a_usable_path(builders, capsys):
    """The printed command is run by the user, from their own directory."""
    builders.make_container("box", arch=HOST_ARCH)
    with pytest.raises(SystemExit) as exc:
        command_login(_login_args("box", get_proot_cmd=True))
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert f"--rootfs={container_rootfs('box')}" in out
    assert "--rootfs=." not in out


def test_a_planted_container_dir_is_refused_outright(builders, tmp_path):
    """The persistent case still stops the command instead of following."""
    host_dir = tmp_path / "host-dir"
    (host_dir / "rootfs").mkdir(parents=True)
    os.symlink(str(host_dir), container_dir("box"))
    with pytest.raises(SystemExit) as exc:
        command_login(_login_args("box"))
    assert exc.value.code == 1
