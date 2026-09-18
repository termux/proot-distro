# Integration tests for the login/run proot-argv assembly, exercised through
# `--get-proot-cmd` (which prints the command and exits, instead of execvpe).

import os
from types import SimpleNamespace

import pytest

import _builders
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


# --- identity variables ------------------------------------------------------
#
# What a guest may ask about where it is running: the container's name,
# the image it was installed from, that image's ID, and the
# `container=` marker systemd and podman set. Every mode, login and run.

_DIGEST = "sha256:" + "cd" * 32


def _identity_container(builders, name="box"):
    manifest = builders.simple_image_manifest(image_ref="debian:bookworm")
    manifest["manifest"]["config"] = {"digest": _DIGEST}
    builders.make_container(name, arch=HOST_ARCH, manifest=manifest)


def _identity_env(capsys, name="box", **over):
    return _builders.login_child_env(
        command_login, _builders.login_args(name, **over), capsys,
    )


def test_identity_vars_reach_the_guest(builders, capsys):
    _identity_container(builders)
    env = _identity_env(capsys)
    assert env["PD_CONTAINER"] == "box"
    assert env["PD_IMAGE"] == "debian:bookworm"
    assert env["PD_IMAGE_ID"] == _DIGEST
    assert env["container"] == "proot-distro"


def test_identity_vars_without_an_image_manifest(builders, capsys):
    # A plain-tarball container: the name and the marker are still
    # there, the image is simply not known.
    builders.make_container("tarbox", arch=HOST_ARCH)
    env = _identity_env(capsys, "tarbox")
    assert env["PD_CONTAINER"] == "tarbox"
    assert env["container"] == "proot-distro"
    assert "PD_IMAGE" not in env
    assert "PD_IMAGE_ID" not in env


@pytest.mark.parametrize("mode", [
    dict(isolated=True), dict(minimal=True),
])
def test_identity_vars_in_every_mode(builders, capsys, mode):
    _identity_container(builders)
    env = _identity_env(capsys, **mode)
    assert env["PD_CONTAINER"] == "box"
    assert env["PD_IMAGE"] == "debian:bookworm"
    assert env["PD_IMAGE_ID"] == _DIGEST
    assert env["container"] == "proot-distro"


def test_users_env_flag_overrides_identity(builders, capsys):
    # The user controls the command line; the image does not (see the
    # security test), but --env is theirs.
    _identity_container(builders)
    env = _identity_env(capsys, env=["PD_CONTAINER=other", "container=x"])
    assert env["PD_CONTAINER"] == "other"
    assert env["container"] == "x"
    assert env["PD_IMAGE"] == "debian:bookworm"


def test_run_carries_identity_vars(builders, capsys):
    manifest = builders.simple_image_manifest(
        image_ref="debian:bookworm", cmd=["/bin/echo", "hi"],
    )
    manifest["manifest"]["config"] = {"digest": _DIGEST}
    builders.make_container("runbox", arch=HOST_ARCH, manifest=manifest)
    args = SimpleNamespace(
        container_name="runbox", run_args=[], get_proot_cmd=True,
        work_dir=None, user="root",
    )
    env = _builders.login_child_env(command_run, args, capsys)
    assert env["PD_CONTAINER"] == "runbox"
    assert env["PD_IMAGE"] == "debian:bookworm"
    assert env["PD_IMAGE_ID"] == _DIGEST
    assert env["container"] == "proot-distro"


# --- the profile.d snippet says what *this* session was handed -------------

def _snippet(name):
    path = os.path.join(container_rootfs(name), "etc", "profile.d",
                        "termux-profile.sh")
    with open(path) as fh:
        return fh.read()


def test_snippet_is_written_by_every_mode_and_host(builders, capsys):
    _identity_container(builders)
    for mode in (dict(isolated=True), dict(minimal=True), {}):
        mark = "-".join(sorted(mode)) or "default"
        _identity_env(capsys, env=[f"MARK={mark}"], **mode)
        content = _snippet("box")
        assert "export PD_CONTAINER='box'" in content
        assert "export PD_IMAGE='debian:bookworm'" in content
        assert f"export MARK='{mark}'" in content
        # Off Termux the prefix is never bound, so no PATH append.
        assert "PATH" not in content


def test_snippet_follows_a_rename(builders, capsys):
    from proot_distro.commands.rename import command_rename

    _identity_container(builders)
    _identity_env(capsys)
    assert "export PD_CONTAINER='box'" in _snippet("box")

    command_rename(SimpleNamespace(orig_name="box", new_name="crate"))
    # An --isolated session used to leave the previous login's snippet
    # in place, so `su -` inside it announced the old name.
    _identity_env(capsys, "crate", isolated=True)
    content = _snippet("crate")
    assert "export PD_CONTAINER='crate'" in content
    assert "'box'" not in content


# --- a refused exec is a message -------------------------------------------

def test_a_refused_exec_is_a_message(builders, capsys, monkeypatch):
    import errno
    from proot_distro.commands import login as login_mod

    _identity_container(builders)
    cwd = os.getcwd()

    def _execvpe(*a, **k):
        raise OSError(errno.E2BIG, "Argument list too long")

    monkeypatch.setattr(login_mod.os, "execvpe", _execvpe)
    try:
        with pytest.raises(SystemExit) as exc:
            command_login(_builders.login_args("box", get_proot_cmd=False))
    finally:
        os.chdir(cwd)      # _exec_proot fchdir'ed into the rootfs
    assert exc.value.code == 1
    assert "cannot execute proot: Argument list too long" in (
        capsys.readouterr().err
    )


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
    /storage, etc. The dalvik-cache and Termux-app bind helpers are
    replaced by recorders that append a label to the returned list.
    """
    monkeypatch.setattr(proot_cmd, "IS_TERMUX", True)
    monkeypatch.setattr(proot_cmd, "system_bindings", lambda: ["--bind=/system"])
    monkeypatch.setattr(proot_cmd, "storage_bindings",
                        lambda: ["--bind=/storage"])
    calls = []
    monkeypatch.setattr(proot_cmd, "_add_dalvik_cache_binds",
                        lambda args: calls.append("dalvik"))
    monkeypatch.setattr(proot_cmd, "_add_termux_app_binds",
                        lambda args: calls.append("termux_app"))
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
    return args, calls


def test_termux_type_binds_dalvik_storage_system(tmp_path, monkeypatch):
    # Termux-type, non-isolated: dalvik caches, shared storage, and Android
    # system dirs are bound, but the host's /data/data/com.termux app dirs
    # and the Termux prefix bridge are not.
    args, calls = _termux_host_proot_args(tmp_path, monkeypatch)
    assert "--bind=/system" in args
    assert "--bind=/storage" in args
    assert calls == ["dalvik"]
    assert not any(a.startswith(f"--bind={TERMUX_PREFIX}") for a in args)


def test_termux_type_isolated_no_host_dirs(tmp_path, monkeypatch):
    # Termux-type, isolated: no host directories at all.
    args, calls = _termux_host_proot_args(
        tmp_path, monkeypatch, isolated=True,
    )
    assert "--bind=/system" not in args
    assert "--bind=/storage" not in args
    assert calls == []


def test_normal_type_binds_android_data_and_storage(tmp_path, monkeypatch):
    # Normal-type, non-isolated: dalvik caches, Termux app dirs, shared
    # storage, system dirs, and the Termux prefix bridge are all bound.
    args, calls = _termux_host_proot_args(
        tmp_path, monkeypatch, dist_type="normal",
        login_uid="0", login_gid="0", login_home="/root",
        inner=["/bin/sh", "-l"],
    )
    assert "--bind=/system" in args
    assert "--bind=/storage" in args
    assert calls == ["dalvik", "termux_app"]
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
