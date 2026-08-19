# Containment tests for the directories proot-distro creates inside a
# rootfs on the *host* side, before proot is exec'd: /tmp (bound in as
# /dev/shm), .l2s (handed to proot as PROOT_L2S_DIR), and the termux-type
# guest's own cache dir.
#
# Every component of those paths is guest- or image-controlled, and
# os.makedirs(exist_ok=True) accepts a symlink to a directory while
# os.chmod() follows one — so naming them was enough to have a host
# directory chmod'ed and mounted into the container.

import os
import stat

import pytest

from proot_distro import dirfd
from proot_distro.commands.login import proot_cmd

HOST_ARCH = os.uname().machine


@pytest.fixture
def env(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    return rootfs, outside


# --- the primitive ---------------------------------------------------------

def test_makedirs_under_refuses_a_symlinked_component(env):
    rootfs, outside = env
    os.symlink(str(outside), str(rootfs / "tmp"))

    assert dirfd.makedirs_under(str(rootfs), ("tmp",), mode=0o1777) is None
    # The mode change did not reach through the link.
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700


def test_makedirs_under_refuses_a_symlinked_parent(env):
    rootfs, outside = env
    os.symlink(str(outside), str(rootfs / "data"))

    assert dirfd.makedirs_under(
        str(rootfs), ("data", "data", "com.termux", "cache")) is None
    assert os.listdir(str(outside)) == []


def test_makedirs_under_refuses_a_file_in_the_way(env):
    rootfs, _outside = env
    (rootfs / "tmp").write_text("not a directory")
    assert dirfd.makedirs_under(str(rootfs), ("tmp",)) is None


def test_makedirs_under_creates_and_chmods(env):
    rootfs, _outside = env
    path = dirfd.makedirs_under(str(rootfs), ("tmp",), mode=0o1777)
    assert path == str(rootfs / "tmp")
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o1777


def test_makedirs_under_creates_nested_levels(env):
    rootfs, _outside = env
    path = dirfd.makedirs_under(
        str(rootfs), ("data", "data", "com.termux", "cache"))
    assert path == str(rootfs / "data" / "data" / "com.termux" / "cache")
    assert os.path.isdir(path)


def test_makedirs_under_is_idempotent(env):
    rootfs, _outside = env
    first = dirfd.makedirs_under(str(rootfs), ("tmp",), mode=0o1777)
    second = dirfd.makedirs_under(str(rootfs), ("tmp",), mode=0o1777)
    assert first == second
    assert os.path.isdir(first)


# --- the /dev/shm bind -----------------------------------------------------

def _proot_args(tmp_path, monkeypatch, rootfs, **over):
    monkeypatch.setattr(proot_cmd, "IS_TERMUX", True)
    monkeypatch.setattr(proot_cmd, "system_bindings", lambda: [])
    monkeypatch.setattr(proot_cmd, "storage_bindings", lambda: [])
    monkeypatch.setattr(proot_cmd, "_add_dalvik_cache_binds", lambda args: None)
    monkeypatch.setattr(proot_cmd, "_add_termux_app_binds", lambda args: None)
    base = dict(
        proot_bin="proot", rootfs=str(rootfs), login_wd="/",
        login_uid="0", login_gid="0", login_home="/root",
        emu_args=[], need_emu=False, target_arch=HOST_ARCH,
        hostname="localhost", kernel_release="6.0-test",
        dist_type="normal", minimal=False, isolated=True,
        no_link2symlink=False, no_sysvipc=False, no_kill_on_exit=False,
        use_shared_home=False, shared_tmp=False, shared_x11=False,
        custom_binds=[], redirect_ports=False, inner=["/bin/sh", "-l"],
    )
    base.update(over)
    return proot_cmd.build_proot_args(**base)


def test_symlinked_tmp_is_not_bound_as_dev_shm(env, tmp_path, monkeypatch):
    # --isolated is deliberate: _add_termux_dev_binds runs regardless of it,
    # so this was a way back in through the mode that binds nothing else.
    rootfs, outside = env
    os.symlink(str(outside), str(rootfs / "tmp"))

    args = _proot_args(tmp_path, monkeypatch, rootfs)

    assert not any(a.endswith(":/dev/shm") for a in args)
    assert not any(str(outside) in a for a in args)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700


def test_plain_tmp_is_still_bound_as_dev_shm(env, tmp_path, monkeypatch):
    rootfs, _outside = env
    args = _proot_args(tmp_path, monkeypatch, rootfs)

    expected = f"--bind={rootfs / 'tmp'}:/dev/shm"
    assert expected in args
    assert stat.S_IMODE(os.stat(str(rootfs / "tmp")).st_mode) == 0o1777
