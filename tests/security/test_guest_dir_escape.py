# Containment tests for the directories proot-distro creates on the *host*
# side before proot is exec'd: the shm store bound in as /dev/shm, the
# guest's own /tmp, .l2s (handed to proot as PROOT_L2S_DIR), and the
# termux-type guest's own cache dir.
#
# Every component of the paths inside the rootfs is guest- or
# image-controlled, and os.makedirs(exist_ok=True) accepts a symlink to a
# directory while os.chmod() follows one — so naming them was enough to
# have a host directory chmod'ed and mounted into the container.
#
# For /dev/shm the descriptor walk is not the whole answer, because proot
# resolves a bind source by name when it mounts it, after every check
# here has run. That is why the store is a sibling of the rootfs and not
# a name inside it: reaching it means writing to the container's own
# directory, which no session confined to the rootfs can do.

import os
import stat
from types import SimpleNamespace

import pytest

from proot_distro import dirfd
from proot_distro.commands.login import proot_cmd
from proot_distro.helpers.build_engine import run_step

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


def _shm_source(args):
    """The path proot is told to mount at /dev/shm, or None."""
    for arg in args:
        if arg.startswith("--bind=") and arg.endswith(":/dev/shm"):
            return arg[len("--bind="):-len(":/dev/shm")]
    return None


def test_shm_store_is_a_sibling_of_the_rootfs(env, tmp_path, monkeypatch):
    # --isolated is deliberate throughout: _add_termux_dev_binds runs
    # regardless of it, so the /dev/shm source was a way back in through
    # the mode that binds nothing else of the host.
    rootfs, _outside = env
    args = _proot_args(tmp_path, monkeypatch, rootfs)

    assert _shm_source(args) == str(tmp_path / "shm")
    assert stat.S_IMODE(os.stat(str(tmp_path / "shm")).st_mode) == 0o1777


def test_no_name_inside_the_rootfs_decides_the_shm_bind(env, tmp_path,
                                                        monkeypatch):
    # The source used to be <rootfs>/tmp, a name every session of the
    # container can write. proot resolves a bind source when it mounts
    # it, so flipping that name to a symlink after the check and before
    # the exec handed the next session a host directory at /dev/shm.
    rootfs, outside = env
    os.symlink(str(outside), str(rootfs / "tmp"))

    args = _proot_args(tmp_path, monkeypatch, rootfs)

    source = _shm_source(args)
    assert source == str(tmp_path / "shm")
    assert not source.startswith(str(rootfs) + os.sep)
    assert not any(str(outside) in a for a in args)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700


def test_symlinked_shm_store_is_not_bound(env, tmp_path, monkeypatch):
    # Reaching the store means writing to the container's own directory,
    # which only a session that already has $TERMUX_PREFIX bound can do.
    # The persistent case is still refused rather than followed.
    rootfs, outside = env
    os.symlink(str(outside), str(tmp_path / "shm"))

    args = _proot_args(tmp_path, monkeypatch, rootfs)

    assert _shm_source(args) is None
    assert not any(str(outside) in a for a in args)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700


def test_the_guest_still_gets_a_tmp(env, tmp_path, monkeypatch):
    rootfs, _outside = env
    _proot_args(tmp_path, monkeypatch, rootfs)

    assert stat.S_IMODE(os.stat(str(rootfs / "tmp")).st_mode) == 0o1777


# --- the same two directories on the build side -----------------------------
#
# `build` creates them for every RUN step, against a rootfs assembled from
# an image the Dockerfile named. Nothing has executed inside it yet, so the
# symlink is the image's, not a guest's — the same hole with a shorter path
# to it.

def _run_stage(tmp_path, rootfs, monkeypatch, request=None):
    """A stand-in Stage, pinned the way the engine pins a real one."""
    monkeypatch.setattr(run_step, "IS_TERMUX", True)
    monkeypatch.setattr(run_step, "setup_fake_sysdata", lambda r, **k: None)
    monkeypatch.setattr(run_step, "fake_sysdata_bindings", lambda r, **k: [])
    monkeypatch.setattr(run_step, "get_proot_bin", lambda: "proot")
    monkeypatch.setattr(run_step, "get_device_cpu_arch", lambda: HOST_ARCH)
    monkeypatch.setattr(run_step, "get_emulator_args", lambda *a: [])
    monkeypatch.setattr(run_step, "resolve_user_for_proot",
                        lambda *a, **k: (0, 0))
    dir_fd = os.open(str(tmp_path), os.O_RDONLY | os.O_DIRECTORY)
    rootfs_fd = os.open(str(rootfs), os.O_RDONLY | os.O_DIRECTORY)
    _OPEN_FDS.extend((dir_fd, rootfs_fd))
    return SimpleNamespace(
        index=0, rootfs_dir=str(rootfs), dir_fd=dir_fd, rootfs_fd=rootfs_fd,
        layers=[], target_arch_pd=HOST_ARCH,
        user="", workdir="/", shell=["/bin/sh", "-c"], env={},
        declared_args=[], args={},
    )


_OPEN_FDS = []


@pytest.fixture(autouse=True)
def _close_stage_fds():
    yield
    while _OPEN_FDS:
        try:
            os.close(_OPEN_FDS.pop())
        except OSError:
            pass


def _exec_proot_args(tmp_path, rootfs, monkeypatch):
    """Run _exec_proot with a stubbed Popen and return the proot argv."""
    stage = _run_stage(tmp_path, rootfs, monkeypatch)
    seen = []

    class _Proc:
        pid = 0

        def wait(self, timeout=None):
            return 0

        returncode = 0

    def _popen(args, **kw):
        seen.append(list(args))
        return _Proc()

    monkeypatch.setattr(run_step.subprocess, "Popen", _popen)
    engine = SimpleNamespace(quiet=True, verbose=False, emulator="")
    run_step._exec_proot(engine, stage, ["true"], None)
    return seen[0]


def test_build_shm_store_is_a_sibling_of_the_rootfs(env, tmp_path,
                                                    monkeypatch):
    rootfs, _outside = env
    args = _exec_proot_args(tmp_path, rootfs, monkeypatch)

    assert _shm_source(args) == str(tmp_path / "shm")
    assert stat.S_IMODE(os.stat(str(tmp_path / "shm")).st_mode) == 0o1777
    assert stat.S_IMODE(os.stat(str(rootfs / "tmp")).st_mode) == 0o1777


def test_build_no_name_inside_the_rootfs_decides_the_shm_bind(env, tmp_path,
                                                              monkeypatch):
    rootfs, outside = env
    os.symlink(str(outside), str(rootfs / "tmp"))

    args = _exec_proot_args(tmp_path, rootfs, monkeypatch)

    assert _shm_source(args) == str(tmp_path / "shm")
    assert not any(str(outside) in a for a in args)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700


def test_build_symlinked_shm_store_is_not_bound(env, tmp_path, monkeypatch):
    rootfs, outside = env
    os.symlink(str(outside), str(tmp_path / "shm"))

    args = _exec_proot_args(tmp_path, rootfs, monkeypatch)

    assert _shm_source(args) is None
    assert not any(str(outside) in a for a in args)
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700


def test_build_symlinked_l2s_leaves_proot_l2s_dir_unset(env, tmp_path,
                                                        monkeypatch):
    rootfs, outside = env
    os.symlink(str(outside), str(rootfs / ".l2s"))
    stage = _run_stage(tmp_path, rootfs, monkeypatch)

    env_out = run_step._build_child_env(stage)

    assert "PROOT_L2S_DIR" not in env_out
    assert os.listdir(str(outside)) == []


def test_build_plain_l2s_is_pinned(env, tmp_path, monkeypatch):
    rootfs, _outside = env
    stage = _run_stage(tmp_path, rootfs, monkeypatch)

    env_out = run_step._build_child_env(stage)

    assert env_out["PROOT_L2S_DIR"] == str(rootfs / ".l2s")
    assert os.path.isdir(str(rootfs / ".l2s"))
