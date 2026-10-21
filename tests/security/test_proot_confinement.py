# A proot that does not translate paths must never be handed a guest.
#
# Ubuntu 26.04's proot 5.4.0 marks a tracee seccomp-driven as soon as
# PTRACE_SETOPTIONS accepts PTRACE_O_TRACESECCOMP, but the build has no
# seccomp filter compiled in: nothing after the first execve is stopped,
# so a session runs the host's binaries against the host's files, and a
# Dockerfile's RUN writes wherever the invoking user can. login and the
# build both ran it as if it were confining.
#
# Each test here hands the real self-test a stand-in proot that behaves
# like that build, and lets the command go as far as it would: if the
# session or the step were started, its command would run on the host
# and leave a file there.

import os
import subprocess
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.login import command_login
from proot_distro.helpers.build_engine import run_step
from proot_distro.helpers.build_engine.errors import BuildError

from _builders import fake_proot


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
def run_exec(monkeypatch):
    """Stand in for execvpe by running what it was given, as a child.

    So a session that was not refused does what it would have done:
    run its command through the proot it was handed.
    """
    seen = {}

    def fake_execvpe(binary, argv, env):
        seen.update(binary=binary, argv=list(argv), env=dict(env))
        subprocess.run([binary] + list(argv[1:]), env=env, timeout=60)
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
def box(builders):
    builders.make_container("box", arch=HOST_ARCH)
    return "box"


def test_login_refuses_a_proot_that_leaks_to_the_host(
    box, tmp_path, monkeypatch, run_exec, capsys, real_proot_probe
):
    proot = fake_proot(tmp_path, "leak")
    monkeypatch.setenv("PD_PROOT_BIN", proot)
    escaped = tmp_path / "escaped"

    with pytest.raises(SystemExit) as exc:
        command_login(_login_args(box, login_cmd=["touch", str(escaped)]))

    assert exc.value.code == 1
    assert "does not confine" in capsys.readouterr().err
    assert run_exec == {}
    assert not escaped.exists()


def test_login_refuses_a_leak_even_with_seccomp_already_off(
    box, tmp_path, monkeypatch, run_exec, real_proot_probe
):
    monkeypatch.setenv("PD_PROOT_BIN", fake_proot(tmp_path, "leak"))
    monkeypatch.setenv("PROOT_NO_SECCOMP", "1")
    escaped = tmp_path / "escaped"

    with pytest.raises(SystemExit) as exc:
        command_login(_login_args(box, login_cmd=["touch", str(escaped)]))

    assert exc.value.code == 1
    assert not escaped.exists()


@pytest.mark.parametrize("minimal", [False, True])
def test_login_turns_seccomp_off_for_a_proot_that_needs_it(
    box, tmp_path, monkeypatch, capsys, real_proot_probe, minimal
):
    # --minimal forwards none of the user's PROOT_* toggles, but this one
    # is what keeps the session inside the container.
    monkeypatch.setenv("PD_PROOT_BIN", fake_proot(tmp_path, "leak-with-seccomp"))
    seen = {}

    def fake_execvpe(binary, argv, env):
        seen["env"] = dict(env)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_execvpe)
    here = os.open(os.curdir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(SystemExit):
            command_login(_login_args(box, minimal=minimal))
    finally:
        os.fchdir(here)
        os.close(here)

    assert seen["env"].get("PROOT_NO_SECCOMP") == "1"
    assert "skips path translation" in capsys.readouterr().err


def test_printed_command_carries_the_toggle_the_session_needs(
    box, builders, tmp_path, monkeypatch, capsys, real_proot_probe
):
    monkeypatch.setenv("PD_PROOT_BIN", fake_proot(tmp_path, "leak-with-seccomp"))
    here = os.open(os.curdir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(SystemExit) as exc:
            command_login(_login_args(box, get_proot_cmd=True))
    finally:
        os.fchdir(here)
        os.close(here)

    assert exc.value.code == 0
    env = builders.parse_proot_cmd_env(capsys.readouterr().out)
    assert env.get("PROOT_NO_SECCOMP") == "1"


def test_login_leaves_a_confining_proot_alone(
    box, tmp_path, monkeypatch, capsys, real_proot_probe
):
    monkeypatch.setenv("PD_PROOT_BIN", fake_proot(tmp_path, "confine"))
    seen = {}

    def fake_execvpe(binary, argv, env):
        seen["env"] = dict(env)
        raise SystemExit(0)

    monkeypatch.setattr(os, "execvpe", fake_execvpe)
    here = os.open(os.curdir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(SystemExit):
            command_login(_login_args(box))
    finally:
        os.fchdir(here)
        os.close(here)

    assert "PROOT_NO_SECCOMP" not in seen["env"]
    assert "Warning" not in capsys.readouterr().err


# --- the build's RUN step ---------------------------------------------------

def _stage(tmp_path, monkeypatch, proot):
    monkeypatch.setattr(run_step, "IS_TERMUX", False)
    monkeypatch.setattr(run_step, "get_proot_bin", lambda: proot)
    monkeypatch.setattr(run_step, "get_device_cpu_arch", lambda: HOST_ARCH)
    monkeypatch.setattr(run_step, "get_emulator_args", lambda *a: [])
    monkeypatch.setattr(run_step, "resolve_user_for_proot",
                        lambda *a, **k: (0, 0))
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    return SimpleNamespace(
        index=0, rootfs_dir=str(rootfs), dir_fd=None, rootfs_fd=None,
        layers=[], target_arch_pd=HOST_ARCH,
        user="", workdir="/", shell=["/bin/sh", "-c"], env={},
        declared_args=[], args={}, base_ref="", base_image_id="",
    )


def test_run_step_refuses_a_proot_that_leaks_to_the_host(
    tmp_path, monkeypatch, real_proot_probe
):
    stage = _stage(tmp_path, monkeypatch, fake_proot(tmp_path, "leak"))
    escaped = tmp_path / "escaped"
    engine = SimpleNamespace(quiet=True, verbose=False, emulator="",
                             warned_host_exec=set(), tmp_root=str(tmp_path))

    with pytest.raises(BuildError) as exc:
        run_step._exec_proot(
            engine, stage, ["/bin/sh", "-c", f"touch '{escaped}'"], None)

    assert "does not confine" in str(exc.value)
    assert not escaped.exists()


def test_run_step_turns_seccomp_off_for_a_proot_that_needs_it(
    tmp_path, monkeypatch, real_proot_probe
):
    stage = _stage(tmp_path, monkeypatch,
                   fake_proot(tmp_path, "leak-with-seccomp"))
    seen = {}

    class _Proc:
        pid = os.getpid()

        def wait(self, timeout=None):
            return 0

    real_popen = subprocess.Popen

    def _popen(args, **kw):
        # The probe goes through subprocess too; only the step is faked.
        if f"--rootfs={stage.rootfs_dir}" not in args:
            return real_popen(args, **kw)
        seen["env"] = dict(kw["env"])
        return _Proc()

    monkeypatch.setattr(run_step.subprocess, "Popen", _popen)
    monkeypatch.setattr(run_step, "_wait_for_step", lambda *a, **k: None)
    monkeypatch.setattr(run_step, "_stop_step", lambda *a, **k: 0)
    engine = SimpleNamespace(quiet=True, verbose=False, emulator="",
                             warned_host_exec=set(), tmp_root=str(tmp_path))

    run_step._exec_proot(engine, stage, ["true"], None)

    assert seen["env"].get("PROOT_NO_SECCOMP") == "1"
