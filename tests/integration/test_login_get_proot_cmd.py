# Integration tests for the login/run proot-argv assembly, exercised through
# `--get-proot-cmd` (which prints the command and exits, instead of execvpe).

import os
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.login import command_login, _detect_dist_type
from proot_distro.commands.login import proot_cmd
from proot_distro.commands.run import command_run
from proot_distro.constants import TERMUX_PREFIX
from proot_distro.paths import container_rootfs


HOST_ARCH = get_device_cpu_arch()


def _login_args(name, **over):
    base = dict(container_name=name, get_proot_cmd=True, user="root",
                kernel=None, hostname="localhost", work_dir="",
                redirect_ports=False, isolated=False, minimal=False,
                shared_home=False, shared_tmp=False, shared_x11=False,
                no_link2symlink=False, no_sysvipc=False, no_kill_on_exit=False,
                bind=[], env=[], login_cmd=[], emulator=None)
    base.update(over)
    return SimpleNamespace(**base)


def _run_login(builders, name, **over):
    builders.make_container(name, arch=HOST_ARCH)
    with pytest.raises(SystemExit) as exc:
        command_login(_login_args(name, **over))
    assert exc.value.code == 0


def test_basic_root_login_cmd(builders, capsys):
    _run_login(builders, "box")
    out = capsys.readouterr().out
    assert f"--rootfs={container_rootfs('box')}" in out
    assert "--change-id=0:0" in out         # non-termux containers get change-id
    assert "--bind=/dev" in out
    assert "--bind=/proc" in out
    assert "--bind=/sys" in out
    assert "/bin/sh" in out and "-l" in out  # interactive login shell
    # Off-Termux: no proot extensions.
    assert "--link2symlink" not in out
    assert "--kill-on-exit" not in out


def test_numeric_user_change_id(builders, capsys):
    builders.make_container("box", arch=HOST_ARCH)
    # tester (uid 1000) uses /bin/bash — make it resolvable.
    builders.write_elf(os.path.join(container_rootfs("box"), "bin", "bash"),
                       HOST_ARCH)
    with pytest.raises(SystemExit) as exc:
        command_login(_login_args("box", user="1000"))
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "--change-id=1000:1000" in out
    assert "--cwd=/home/tester" in out


def test_custom_bind_and_env(tmp_path, builders, capsys):
    extra = tmp_path / "share"
    extra.mkdir()
    _run_login(builders, "box", bind=[f"{extra}:/mnt/share"], env=["FOO=bar"])
    out = capsys.readouterr().out
    assert f"--bind={extra}:/mnt/share" in out
    assert "FOO=bar" in out


def test_login_cmd_wrapped(builders, capsys):
    _run_login(builders, "box", login_cmd=["echo", "hi"])
    out = capsys.readouterr().out
    assert "/bin/sh" in out
    assert "-c" in out


def test_missing_shell_errors(builders, capsys):
    builders.make_container("box", arch=HOST_ARCH)
    os.remove(os.path.join(container_rootfs("box"), "bin", "sh"))
    with pytest.raises(SystemExit) as exc:
        command_login(_login_args("box"))
    assert exc.value.code == 1
    assert "not available" in capsys.readouterr().err


def test_run_uses_image_cmd(builders, capsys):
    builders.make_container("runbox", arch=HOST_ARCH, manifest={
        "image_config": {"config": {"Cmd": ["/bin/echo", "hi"]}},
    })
    args = SimpleNamespace(
        container_name="runbox", run_args=[], get_proot_cmd=True,
        work_dir=None, user="root",
    )
    with pytest.raises(SystemExit) as exc:
        command_run(args)
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "/bin/echo" in out
    assert "hi" in out


def test_detect_dist_type_normal(builders):
    builders.make_container("box", arch=HOST_ARCH)
    assert _detect_dist_type(container_rootfs("box")) == "normal"


def test_detect_dist_type_termux(builders):
    builders.make_container("tbox", arch=HOST_ARCH)
    login_bin = os.path.join(
        container_rootfs("tbox") + TERMUX_PREFIX, "bin", "login"
    )
    os.makedirs(os.path.dirname(login_bin), exist_ok=True)
    with open(login_bin, "w") as fh:
        fh.write("#!/bin/sh\n")
    assert _detect_dist_type(container_rootfs("tbox")) == "termux"


def test_termux_branch_adds_proot_extensions(tmp_path, monkeypatch):
    # Force the Termux code path in proot_cmd to verify the extension flags.
    monkeypatch.setattr(proot_cmd, "IS_TERMUX", True)
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    args = proot_cmd.build_proot_args(
        proot_bin="proot", rootfs=str(rootfs), login_wd="/root",
        login_uid="0", login_gid="0", login_home="/root",
        emu_args=[], need_emu=False, target_arch=HOST_ARCH,
        hostname="localhost", kernel_release="6.0-test",
        dist_type="normal", minimal=False, isolated=True,
        no_link2symlink=False, no_sysvipc=False, no_kill_on_exit=False,
        use_shared_home=False, shared_tmp=False, shared_x11=False,
        custom_binds=[], redirect_ports=False, inner=["/bin/sh", "-l"],
    )
    assert "--link2symlink" in args
    assert "--kill-on-exit" in args
    assert "--change-id=0:0" in args


def _termux_host_proot_args(tmp_path, monkeypatch, **over):
    """build_proot_args under a faked Termux host with sentinel bind helpers.

    storage/system bindings are stubbed to fixed sentinels so the dispatch
    logic can be asserted independently of the test host's real /system,
    /storage, etc. _add_android_data_binds is replaced by a recorder.
    """
    monkeypatch.setattr(proot_cmd, "IS_TERMUX", True)
    monkeypatch.setattr(proot_cmd, "system_bindings", lambda: ["--bind=/system"])
    monkeypatch.setattr(proot_cmd, "storage_bindings",
                        lambda: ["--bind=/storage"])
    android_calls = []
    monkeypatch.setattr(proot_cmd, "_add_android_data_binds",
                        lambda args: android_calls.append(True))
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    base = dict(
        proot_bin="proot", rootfs=str(rootfs), login_wd="/",
        login_uid=None, login_gid=None, login_home=None,
        emu_args=[], need_emu=False, target_arch=HOST_ARCH,
        hostname="localhost", kernel_release="6.0-test",
        dist_type="termux", minimal=False, isolated=False,
        no_link2symlink=False, no_sysvipc=False, no_kill_on_exit=False,
        use_shared_home=False, shared_tmp=False, shared_x11=False,
        custom_binds=[], redirect_ports=False, inner=["/bin/login"],
    )
    base.update(over)
    args = proot_cmd.build_proot_args(**base)
    return args, android_calls


def test_termux_type_binds_system_and_storage(tmp_path, monkeypatch):
    # Termux-type, non-isolated: Android system dirs and shared storage
    # are bound, but the host's /data/data/com.termux and the Termux
    # prefix bridge are not.
    args, android_calls = _termux_host_proot_args(tmp_path, monkeypatch)
    assert "--bind=/system" in args
    assert "--bind=/storage" in args
    assert android_calls == []
    assert not any(a.startswith(f"--bind={TERMUX_PREFIX}") for a in args)


def test_termux_type_isolated_no_host_dirs(tmp_path, monkeypatch):
    # Termux-type, isolated: no host directories at all.
    args, android_calls = _termux_host_proot_args(
        tmp_path, monkeypatch, isolated=True,
    )
    assert "--bind=/system" not in args
    assert "--bind=/storage" not in args
    assert android_calls == []


def test_normal_type_binds_android_data_and_storage(tmp_path, monkeypatch):
    # Normal-type, non-isolated: Android data dirs, shared storage, system
    # dirs, and the Termux prefix bridge are all bound.
    args, android_calls = _termux_host_proot_args(
        tmp_path, monkeypatch, dist_type="normal",
        login_uid="0", login_gid="0", login_home="/root",
        inner=["/bin/sh", "-l"],
    )
    assert "--bind=/system" in args
    assert "--bind=/storage" in args
    assert android_calls == [True]
    assert f"--bind={TERMUX_PREFIX}" in args


def test_minimal_login_keeps_image_env(builders, capsys):
    # Minimal mode no longer discards the image manifest's Env entries.
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_config": {"config": {"Env": ["FOO=frommanifest"]}},
    })
    with pytest.raises(SystemExit) as exc:
        command_login(_login_args("box", minimal=True))
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "FOO=frommanifest" in out


def test_termux_type_login_applies_image_env(builders, capsys):
    # Termux-type containers now apply the image manifest's Env entries.
    builders.make_container("tbox", arch=HOST_ARCH, manifest={
        "image_config": {"config": {"Env": ["FOO=frommanifest"]}},
    })
    login_bin = os.path.join(
        container_rootfs("tbox") + TERMUX_PREFIX, "bin", "login"
    )
    os.makedirs(os.path.dirname(login_bin), exist_ok=True)
    with open(login_bin, "w") as fh:
        fh.write("#!/bin/sh\n")
    with pytest.raises(SystemExit) as exc:
        command_login(_login_args("tbox"))
    assert exc.value.code == 0
    out = capsys.readouterr().out
    assert "FOO=frommanifest" in out
