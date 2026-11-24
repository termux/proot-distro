# Integration test for proot_distro.commands.login.detach.spawn_detached.
#
# Exercises the real double-fork daemonization end to end using a benign
# `sleep` process in place of proot: the daemon must register itself in
# the session registry (with the GRANDCHILD pid, since register_session
# runs there), keep the entry alive via the inherited flock across exec,
# and have the entry self-pruned by `ps` once it is killed.

import os
import shutil
import signal
import threading
import time

import pytest

from proot_distro import session
from proot_distro import session_runtime
from proot_distro.operations.errors import SessionExecutionFailure
from proot_distro.commands.login.detach import spawn_detached

pytestmark = pytest.mark.skipif(
    shutil.which("sleep") is None, reason="needs the `sleep` binary"
)


def _wait_for(predicate, timeout=4.0):
    deadline = time.time() + timeout
    while time.time() < deadline:
        val = predicate()
        if val:
            return val
        time.sleep(0.02)
    return predicate()


def _find(pid):
    return next(
        (s for s in session.active_sessions() if s.get("pid") == pid), None
    )


def test_spawn_detached_registers_then_prunes_on_kill():
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    pid = spawn_detached(
        "sleep", ["sleep", "30"], env,
        register_kwargs=dict(
            container="ubuntu", kind="run",
            command_argv=["sleep", "30"], user="root", detach=True,
        ),
    )
    assert isinstance(pid, int) and pid > 0

    try:
        rec = _wait_for(lambda: _find(pid))
        assert rec is not None
        assert rec["container"] == "ubuntu"
        assert rec["kind"] == "run"
        assert rec["detach"] is True
        # The reported PID is a real, live process (the exec'd `sleep`).
        os.kill(pid, 0)
        assert os.getsid(pid) != pid
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass

    # Death drops the inherited flock; the next listing prunes the entry.
    assert _wait_for(lambda: _find(pid) is None) is True


def test_spawn_detached_reports_a_refused_exec():
    """A daemon whose exec is refused is not a session.

    The PID used to be written before the exec and the pipe closed, so
    the foreground reported "Started detached session" with a PID that
    had already _exit'ed. Now the reason follows the PID and the
    foreground answers None and says why.
    """
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"),
           "X": "a\x00b"}                       # ValueError before execve
    with pytest.raises(SessionExecutionFailure) as exc:
        spawn_detached(
            "sleep", ["sleep", "30"], env,
            register_kwargs=dict(
                container="ubuntu", kind="run",
                command_argv=["sleep", "30"], user="root", detach=True,
            ),
        )
    assert "cannot execute proot" in str(exc.value)


def test_spawn_detached_reports_e2big():
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    # Each string is within execve's per-string limit; together they are
    # past ARG_MAX, which is the case a per-entry cap cannot cover.
    for i in range(64):
        env[f"V{i}"] = "x" * 100_000
    with pytest.raises(SessionExecutionFailure) as exc:
        spawn_detached(
            "sleep", ["sleep", "30"], env,
            register_kwargs=dict(
                container="ubuntu", kind="run",
                command_argv=["sleep", "30"], user="root", detach=True,
            ),
        )
    assert "Argument list too long" in str(exc.value)


def test_spawn_detached_never_forks_in_multithreaded_caller(monkeypatch):
    """Only the fresh launcher may fork; the application process must not."""
    release = threading.Event()
    ready = threading.Event()

    def background():
        ready.set()
        release.wait(10)

    thread = threading.Thread(target=background)
    thread.start()
    assert ready.wait(5)

    def forbidden_fork():
        raise AssertionError("spawn_detached forked in its caller")

    monkeypatch.setattr(session_runtime.os, "fork", forbidden_fork)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    pid = None
    try:
        pid = spawn_detached(
            "sleep", ["sleep", "30"], env,
            register_kwargs=dict(
                container="ubuntu", kind="run",
                command_argv=["sleep", "30"], user="root", detach=True,
            ),
        )
        assert thread.is_alive()
        os.kill(pid, 0)
    finally:
        if pid is not None:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        release.set()
        thread.join(10)

    assert not thread.is_alive()


def test_spawn_detached_finds_launcher_outside_project_directory(
        tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}
    pid = spawn_detached(
        "sleep", ["sleep", "30"], env,
        register_kwargs=dict(
            container="ubuntu", kind="run",
            command_argv=["sleep", "30"], user="root", detach=True,
        ),
    )
    try:
        os.kill(pid, 0)
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def test_spawn_detached_ignores_python_startup_hooks(tmp_path, monkeypatch):
    marker = tmp_path / "sitecustomize-ran"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('loaded')\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(hooks))
    monkeypatch.setenv("PYTHONINSPECT", "1")
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    pid = spawn_detached(
        "sleep", ["sleep", "30"], env,
        register_kwargs=dict(
            container="ubuntu", kind="run",
            command_argv=["sleep", "30"], user="root", detach=True,
        ),
    )
    try:
        assert not marker.exists()
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def test_spawn_detached_ignores_package_shadow_in_working_directory(
        tmp_path, monkeypatch):
    marker = tmp_path / "shadow-package-loaded"
    shadow = tmp_path / "proot_distro"
    shadow.mkdir()
    (shadow / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('loaded')\n"
    )
    (shadow / "_detach_worker.py").write_text(
        "raise RuntimeError('shadow launcher executed')\n"
    )
    monkeypatch.chdir(tmp_path)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    pid = spawn_detached(
        "sleep", ["sleep", "30"], env,
        register_kwargs=dict(
            container="ubuntu", kind="run",
            command_argv=["sleep", "30"], user="root", detach=True,
        ),
    )
    try:
        assert not marker.exists()
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def test_spawn_detached_does_not_forward_unrelated_ambient_environment(
        monkeypatch):
    for index in range(32):
        monkeypatch.setenv(f"UNRELATED_LARGE_VALUE_{index}", "x" * 100_000)
    env = {"PATH": os.environ.get("PATH", "/usr/bin:/bin")}

    pid = spawn_detached(
        "sleep", ["sleep", "30"], env,
        register_kwargs=dict(
            container="ubuntu", kind="run",
            command_argv=["sleep", "30"], user="root", detach=True,
        ),
    )
    try:
        os.kill(pid, 0)
    finally:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def test_detach_launcher_environment_excludes_application_secrets(
        tmp_path, monkeypatch):
    monkeypatch.setenv("PD_DOCKER_AUTH", "user:secret")
    monkeypatch.setenv("APPLICATION_SECRET", "secret")
    monkeypatch.setattr(session_runtime, "RUNTIME_DIR", "relative-state")
    monkeypatch.setattr(session_runtime, "BASE_CACHE_DIR", "relative-cache")
    monkeypatch.chdir(tmp_path)

    env = session_runtime._launcher_environment()

    assert "PD_DOCKER_AUTH" not in env
    assert "APPLICATION_SECRET" not in env
    assert env["_PROOT_DISTRO_API_RUNTIME_DIR"] == str(
        tmp_path / "relative-state"
    )
    assert env["_PROOT_DISTRO_API_CACHE_DIR"] == str(
        tmp_path / "relative-cache"
    )


def test_spawn_detached_bounds_launcher_request(monkeypatch):
    monkeypatch.setattr(session_runtime, "_MAX_DETACH_REQUEST_BYTES", 128)

    def forbidden_popen(*_args, **_kwargs):
        raise AssertionError("oversized request started a launcher")

    monkeypatch.setattr(session_runtime.subprocess, "Popen", forbidden_popen)
    with pytest.raises(SessionExecutionFailure, match="size limit"):
        spawn_detached(
            "sleep", ["sleep", "30"], {"VALUE": "x" * 256},
            register_kwargs=dict(
                container="ubuntu", kind="run",
                command_argv=["sleep", "30"], user="root", detach=True,
            ),
        )


@pytest.mark.parametrize("response", [
    b"", b"[]", b"not json", b'{"pid":123,"unexpected":true}',
])
def test_spawn_detached_rejects_invalid_launcher_response(
        monkeypatch, response):
    class Process:
        returncode = 0

        def communicate(self, _request):
            return response, b""

    monkeypatch.setattr(
        session_runtime.subprocess, "Popen", lambda *_a, **_k: Process()
    )
    with pytest.raises(SessionExecutionFailure, match="invalid response"):
        spawn_detached(
            "sleep", ["sleep", "30"], {},
            register_kwargs=dict(
                container="ubuntu", kind="run", command_argv=["sleep"],
                user="root", detach=True,
            ),
        )


def test_spawn_detached_normalizes_launcher_channel_failure(monkeypatch):
    class Process:
        def __init__(self):
            self.killed = False
            self.waited = False

        def communicate(self, _request):
            raise OSError("channel unavailable")

        def kill(self):
            self.killed = True

        def wait(self):
            self.waited = True

    process = Process()
    monkeypatch.setattr(
        session_runtime.subprocess, "Popen", lambda *_a, **_k: process
    )

    with pytest.raises(
        SessionExecutionFailure, match="launcher communication failed"
    ):
        spawn_detached(
            "sleep", ["sleep", "30"], {},
            register_kwargs=dict(
                container="ubuntu", kind="run", command_argv=["sleep"],
                user="root", detach=True,
            ),
        )

    assert process.killed
    assert process.waited
