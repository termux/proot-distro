import os
import subprocess
import threading
import time

import pytest

from proot_distro.api import (
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


def test_cancelled_session_setup_does_not_start_worker(builders, fake_proot):
    builders.make_container("cancelled-box")
    cancelled = threading.Event()
    cancelled.set()

    with pytest.raises(OperationCancelledError) as exc:
        _program(fake_proot).start(
            "cancelled-box", cancel_event=cancelled
        )

    assert exc.value.operation == "start"


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
