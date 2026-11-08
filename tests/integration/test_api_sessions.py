import os
import subprocess

import pytest

from proot_distro.api import (
    InvalidArgumentError,
    OperationError,
    ProotDistro,
    RuntimeConfig,
    SessionProcess,
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


def test_start_failure_is_operation_error_without_terminal_output(
        builders, tmp_path, capsys):
    builders.make_container("box")
    missing = tmp_path / "missing-proot"

    with pytest.raises(OperationError) as exc:
        _program(missing).start("box")

    assert exc.value.operation == "start"
    assert "proot" in str(exc.value).lower()
    assert capsys.readouterr() == ("", "")


@pytest.mark.parametrize("method,args,kwargs,operation", [
    ("start", ("bad/name",), {}, "start"),
    ("start", ("box", "echo"), {}, "start"),
    ("start", ("box",), {"environment": {"BAD=KEY": "x"}}, "start"),
    ("start", ("box",), {"isolated": True, "minimal": True}, "start"),
    ("run", ("box", ["bad\0arg"]), {}, "run"),
    ("kill_session", (0,), {}, "kill_session"),
    ("kill_session", (123,), {"signal_name": "NOPE"}, "kill_session"),
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
