import os
import subprocess
import threading
import time

import pytest

from proot_distro.api import (
    BusyError,
    DetachedSession,
    EventKind,
    InvalidArgumentError,
    OperationError,
    OperationCancelledError,
    NotFoundError,
    ProotDistro,
    RuntimeConfig,
    SessionProcess,
    SessionCommand,
    SessionSignalResult,
)
from proot_distro.paths import container_rootfs


def _program(fake_proot):
    default = RuntimeConfig.default()
    return ProotDistro(RuntimeConfig(
        default.runtime_dir, default.cache_dir, fake_proot
    ))


@pytest.fixture
def fake_proot(tmp_path):
    path = tmp_path / "fake-proot"
    path.write_text(
        "#!/bin/sh\n"
        "if [ \"$FAKE_PROOT_SLEEP\" = 1 ]; then\n"
        "  if [ \"$FAKE_PROOT_OUTPUT\" = 1 ]; then\n"
        "    printf 'detached stdout\\n'\n"
        "    printf 'detached stderr\\n' >&2\n"
        "  fi\n"
        "  exec sleep 30\n"
        "fi\n"
        "printf 'ARGS:%s\\n' \"$*\"\n"
        "printf 'API_VALUE:%s\\n' \"$API_VALUE\"\n"
    )
    path.chmod(0o755)
    return path


def test_start_returns_process_handle_with_piped_output(builders, fake_proot):
    builders.make_container("box")

    process = _program(fake_proot).start(
        "box",
        ["echo", "hello"],
        environment={"API_VALUE": "visible", "EMPTY": ""},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate(timeout=10)

    assert isinstance(process, SessionProcess)
    assert process.kind == "login"
    assert process.container == "box"
    assert process.returncode == 0
    assert b"echo" in stdout and b"hello" in stdout
    assert b"API_VALUE:visible" in stdout
    assert stderr == b""


def test_session_worker_ignores_startup_hooks_and_cwd_package_shadow(
        tmp_path, builders, fake_proot, monkeypatch):
    builders.make_container("safe-worker")
    site_marker = tmp_path / "session-sitecustomize-ran"
    shadow_marker = tmp_path / "session-shadow-package-loaded"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(site_marker)!r}).write_text('loaded')\n"
    )
    working = tmp_path / "working"
    shadow = working / "proot_distro"
    shadow.mkdir(parents=True)
    (shadow / "__init__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(shadow_marker)!r}).write_text('loaded')\n"
    )
    monkeypatch.setenv("PYTHONPATH", str(hooks))
    monkeypatch.setenv("PYTHONINSPECT", "1")
    monkeypatch.chdir(working)

    process = _program(fake_proot).start(
        "safe-worker", ["true"],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    process.communicate(timeout=10)

    assert process.returncode == 0
    assert not site_marker.exists()
    assert not shadow_marker.exists()


def test_run_uses_image_entrypoint_and_arguments(builders, fake_proot):
    builders.make_container("runbox", manifest={
        "image_config": {"config": {
            "Entrypoint": ["/bin/echo"],
            "Cmd": ["default"],
        }},
    })

    process = _program(fake_proot).run(
        "runbox", ["replacement"], stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    stdout, stderr = process.communicate(timeout=10)

    assert process.kind == "run"
    assert b"/bin/echo replacement" in stdout
    assert stderr == b""


def test_session_process_context_terminates_and_reaps(builders, fake_proot):
    builders.make_container("context-box")
    pd = _program(fake_proot)

    with pd.start(
        "context-box",
        environment={"FAKE_PROOT_SLEEP": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    ) as process:
        pid = process.pid
        assert process.poll() is None

    assert process.returncode is not None
    assert all(session.pid != pid for session in pd.list_sessions())


def test_foreground_setup_warnings_stream_as_events(
        builders, fake_proot, tmp_path):
    builders.make_container("warning-box")
    first = tmp_path / "first"
    second = tmp_path / "second"
    first.mkdir()
    second.mkdir()
    events = []
    default = RuntimeConfig.default()
    pd = ProotDistro(RuntimeConfig(
        default.runtime_dir, default.cache_dir, fake_proot
    ), event_handler=events.append)

    process = pd.start(
        "warning-box", binds=[f"{first}:/mnt", f"{second}:/mnt"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    process.wait(timeout=10)

    assert any(
        event.operation == "start"
        and event.kind == EventKind.WARNING
        and "overlaps" in event.message
        and event.phase == "session-setup"
        and event.resource == "warning-box"
        for event in events
    )


def test_setup_event_handler_exception_stops_startup(
        builders, fake_proot, tmp_path):
    builders.make_container("warning-failure")
    first = tmp_path / "first-failure"
    second = tmp_path / "second-failure"
    first.mkdir()
    second.mkdir()
    expected = LookupError("event destination unavailable")

    def fail(_event):
        raise expected

    default = RuntimeConfig.default()
    pd = ProotDistro(RuntimeConfig(
        default.runtime_dir, default.cache_dir, fake_proot
    ), event_handler=fail)

    with pytest.raises(LookupError) as exc:
        pd.start(
            "warning-failure",
            binds=[f"{first}:/mnt", f"{second}:/mnt"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    assert exc.value is expected
    assert pd.list_sessions() == ()


@pytest.mark.skipif(
    not os.path.isdir("/proc/self/fd"), reason="needs procfs descriptor data"
)
def test_repeated_setup_callback_failures_release_resources(
    builders, fake_proot, tmp_path,
):
    builders.make_container("warning-resource")
    first = tmp_path / "first-resource"
    second = tmp_path / "second-resource"
    first.mkdir()
    second.mkdir()
    before_fds = len(os.listdir("/proc/self/fd"))
    before_threads = set(threading.enumerate())
    default = RuntimeConfig.default()
    config = RuntimeConfig(
        default.runtime_dir, default.cache_dir, fake_proot
    )

    for index in range(10):
        expected = LookupError(f"setup callback failure {index}")

        def fail(_event, expected=expected):
            raise expected

        pd = ProotDistro(config, event_handler=fail)
        with pytest.raises(LookupError) as exc:
            pd.start(
                "warning-resource",
                binds=[f"{first}:/mnt", f"{second}:/mnt"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            )
        assert exc.value is expected
        assert pd.list_sessions() == ()

    process = ProotDistro(config).start(
        "warning-resource", ["true"],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    process.wait(timeout=10)

    assert len(os.listdir("/proc/self/fd")) <= before_fds
    assert set(threading.enumerate()) <= before_threads
    assert ProotDistro(config).list_sessions() == ()


def test_cancelled_session_setup_does_not_start_worker(builders, fake_proot):
    builders.make_container("cancelled-box")
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(OperationCancelledError) as exc:
        _program(fake_proot).start(
            "cancelled-box", cancel_event=cancelled
        )

    assert exc.value.operation == "start"


def test_pre_cancelled_detached_setup_does_not_start_session(
        builders, fake_proot):
    builders.make_container("cancelled-detached")
    cancelled = threading.Event()
    cancelled.set()
    pd = _program(fake_proot)

    with pytest.raises(OperationCancelledError) as exc:
        pd.start_detached(
            "cancelled-detached", cancel_event=cancelled
        )

    assert exc.value.operation == "start_detached"
    assert pd.list_sessions() == ()


def test_inspect_start_returns_path_based_launch_description(
        builders, fake_proot, tmp_path):
    builders.make_container("inspect-box")
    bind = tmp_path / "bind"
    bind.mkdir()
    pd = _program(fake_proot)

    command = pd.inspect_start(
        "inspect-box",
        ["echo", "hello"],
        environment={"API_VALUE": "inspect"},
        binds=[f"{bind}:/mnt/bind"],
        work_dir="/root",
    )

    assert isinstance(command, SessionCommand)
    assert command.kind == "login"
    assert command.container == "inspect-box"
    assert command.executable == str(fake_proot)
    assert command.argv[0] == str(fake_proot)
    assert f"--rootfs={command.rootfs}" in command.argv
    assert "--rootfs=." not in command.argv
    assert "--cwd=/root" in command.argv
    assert any(str(bind) in argument for argument in command.argv)
    assert command.environment_dict["API_VALUE"] == "inspect"
    assert any("echo" in argument for argument in command.inner_command)
    assert command.shell_command.startswith("env ")
    assert str(fake_proot) in command.shell_command
    assert pd.list_sessions() == ()


def test_inspect_run_resolves_image_entrypoint(builders, fake_proot):
    builders.make_container("inspect-run", manifest={
        "image_config": {"config": {
            "Entrypoint": ["/bin/server"],
            "Cmd": ["default"],
            "WorkingDir": "/srv",
        }},
    })

    command = _program(fake_proot).inspect_run(
        "inspect-run", ["replacement"]
    )

    assert command.kind == "run"
    assert command.inner_command == ("/bin/server", "replacement")
    assert command.work_dir == "/srv"
    assert "--cwd=/srv" in command.argv


def test_live_session_is_listed_and_terminated_as_tree(builders, fake_proot):
    builders.make_container("sleepbox")
    pd = _program(fake_proot)

    process = pd.start(
        "sleepbox",
        environment={"FAKE_PROOT_SLEEP": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    sessions = pd.list_sessions()

    assert any(session.pid == process.pid for session in sessions)
    result = process.terminate()

    assert isinstance(result, SessionSignalResult)
    assert process.pid in result.pids
    assert result.signal == "SIGTERM"
    assert process.wait(timeout=10) is not None


def test_detached_session_is_registered_and_can_be_stopped(
        builders, fake_proot):
    builders.make_container("detached-box")
    pd = _program(fake_proot)

    session = pd.start_detached(
        "detached-box", environment={"FAKE_PROOT_SLEEP": "1"}
    )
    try:
        assert isinstance(session, DetachedSession)
        assert session.kind == "login"
        records = pd.list_sessions()
        assert any(record.pid == session.pid and record.detached
                   for record in records)
    finally:
        pd.kill_session(session.pid, signal_name="KILL")


def test_detached_session_inherits_container_lock(builders, fake_proot):
    builders.make_container("detached-lock")
    pd = _program(fake_proot)

    session = pd.start_detached(
        "detached-lock", environment={"FAKE_PROOT_SLEEP": "1"}
    )
    try:
        with pytest.raises(BusyError) as exc:
            pd.remove_container("detached-lock")
        assert exc.value.operation == "remove_container"
        assert os.path.isdir(container_rootfs("detached-lock"))
    finally:
        pd.kill_session(session.pid, signal_name="KILL")

    removed = pd.remove_container("detached-lock")
    assert removed.name == "detached-lock"


def test_detached_session_uses_configured_state_roots(
        tmp_path, builders, fake_proot):
    archive = tmp_path / "configured-rootfs.tar"
    builders.make_tar(str(archive), builders.rootfs_members())
    pd = ProotDistro(RuntimeConfig(
        tmp_path / "state", tmp_path / "cache", fake_proot
    ))
    pd.install(archive, name="configured-detached")

    session = pd.start_detached(
        "configured-detached", environment={"FAKE_PROOT_SLEEP": "1"}
    )
    try:
        assert any(item.pid == session.pid for item in pd.list_sessions())
        assert ProotDistro().list_sessions() == ()
        with pytest.raises(BusyError):
            pd.remove_container("configured-detached")
    finally:
        pd.kill_session(session.pid, signal_name="KILL")

    assert pd.list_sessions() == ()
    pd.remove_container("configured-detached")


def test_detached_session_appends_stdout_and_stderr(
        builders, fake_proot, tmp_path):
    builders.make_container("detached-output")
    stdout_path = tmp_path / "stdout.log"
    stderr_path = tmp_path / "stderr.log"
    stdout_path.write_text("existing stdout\n")
    stderr_path.write_text("existing stderr\n")
    pd = _program(fake_proot)

    session = pd.start_detached(
        "detached-output",
        environment={
            "FAKE_PROOT_SLEEP": "1",
            "FAKE_PROOT_OUTPUT": "1",
        },
        stdout=stdout_path,
        stderr=stderr_path,
    )
    try:
        deadline = time.monotonic() + 5
        while (time.monotonic() < deadline
               and "detached stdout" not in stdout_path.read_text()):
            time.sleep(0.01)
        assert stdout_path.read_text() == "existing stdout\ndetached stdout\n"
        assert stderr_path.read_text() == "existing stderr\ndetached stderr\n"
    finally:
        pd.kill_session(session.pid, signal_name="KILL")


def test_detached_session_can_merge_stderr_into_stdout(
        builders, fake_proot, tmp_path):
    builders.make_container("detached-merged-output")
    output_path = tmp_path / "combined.log"
    pd = _program(fake_proot)

    session = pd.start_detached(
        "detached-merged-output",
        environment={
            "FAKE_PROOT_SLEEP": "1",
            "FAKE_PROOT_OUTPUT": "1",
        },
        stdout=output_path,
        stderr=subprocess.STDOUT,
    )
    try:
        deadline = time.monotonic() + 5
        while (time.monotonic() < deadline
               and "detached stderr" not in output_path.read_text()):
            time.sleep(0.01)
        assert output_path.read_text() == (
            "detached stdout\ndetached stderr\n"
        )
    finally:
        pd.kill_session(session.pid, signal_name="KILL")


def test_run_detached_and_kill_all_sessions(builders, fake_proot):
    builders.make_container("run-detached", manifest={
        "image_config": {"config": {"Cmd": ["/bin/server"]}},
    })
    builders.make_container("login-detached")
    pd = _program(fake_proot)
    try:
        first = pd.run_detached(
            "run-detached", environment={"FAKE_PROOT_SLEEP": "1"}
        )
        second = pd.start_detached(
            "login-detached", ["server"],
            environment={"FAKE_PROOT_SLEEP": "1"},
        )
        result = pd.kill_all_sessions(signal_name="KILL")
        assert set(result.pids) >= {first.pid, second.pid}
        assert result.target == "*"
        assert result.signal == "SIGKILL"
        assert pd.list_sessions() == ()
    finally:
        pd.kill_all_sessions(signal_name="KILL")


def test_start_failure_is_operation_error_without_terminal_output(
        builders, tmp_path, capsys):
    builders.make_container("box")
    missing = tmp_path / "missing-proot"

    with pytest.raises(OperationError) as exc:
        _program(missing).start("box")

    assert exc.value.operation == "start"
    assert "proot" in str(exc.value).lower()
    assert capsys.readouterr() == ("", "")


def test_start_missing_container_is_typed_error(fake_proot):
    with pytest.raises(NotFoundError) as exc:
        _program(fake_proot).start("missing")
    assert exc.value.operation == "start"


@pytest.mark.parametrize("method,args,kwargs,operation", [
    ("start", ("bad/name",), {}, "start"),
    ("start", ("box", "echo"), {}, "start"),
    ("start", ("box",), {"environment": {"BAD=KEY": "x"}}, "start"),
    ("start", ("box",), {"isolated": True, "minimal": True}, "start"),
    ("run", ("box", ["bad\0arg"]), {}, "run"),
    ("start", ("box",), {"startup_timeout": -1}, "start"),
    ("run", ("box",), {"cancel_event": object()}, "run"),
    ("start_detached", ("box",), {"cancel_event": object()},
     "start_detached"),
    ("start_detached", ("box", "echo"), {}, "start_detached"),
    ("start_detached", ("box",), {"stdout": subprocess.PIPE},
     "start_detached"),
    ("run_detached", ("box",), {"environment": []}, "run_detached"),
    ("inspect_start", ("box", "echo"), {}, "inspect_start"),
    ("inspect_run", ("box",), {"minimal": 1}, "inspect_run"),
    ("kill_session", (0,), {}, "kill_session"),
    ("kill_session", (123,), {"signal_name": "NOPE"}, "kill_session"),
    ("kill_all_sessions", (), {"signal_name": "NOPE"},
     "kill_all_sessions"),
])
def test_session_validation_happens_before_process_start(
        method, args, kwargs, operation, monkeypatch):
    monkeypatch.setattr(
        "proot_distro.api.program.subprocess.Popen",
        lambda *_args, **_kwargs: pytest.fail("process should not start"),
    )

    with pytest.raises(InvalidArgumentError) as exc:
        getattr(ProotDistro(), method)(*args, **kwargs)

    assert exc.value.operation == operation
