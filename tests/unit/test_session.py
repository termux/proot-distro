# Tests for proot_distro.session (active-session registry) and the
# formatting/dispatch of the `ps` command.

import fcntl
import json
import os

from types import SimpleNamespace

from proot_distro import constants, session
from proot_distro.commands.ps import command_ps, _fmt_uptime, _fmt_command


def _session_path(pid):
    return os.path.join(constants.SESSIONS_DIR, f"{pid}.json")


def _make_live(pid, container, kind="run", command=None, user="root",
               start_time=1.0):
    """Create a session file and hold its exclusive lock via a live fd."""
    os.makedirs(constants.SESSIONS_DIR, exist_ok=True)
    fd = open(_session_path(pid), "w")
    json.dump(
        {
            "pid": pid, "container": container, "kind": kind,
            "command": command or ["sh"], "user": user,
            "start_time": start_time,
        },
        fd,
    )
    fd.flush()
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    return fd


# ---------------------------------------------------------------------------
# register_session / active_sessions lifecycle
# ---------------------------------------------------------------------------

def test_register_then_listed_then_pruned_on_close():
    fd = session.register_session(
        container="ubuntu", kind="login",
        command_argv=["/bin/bash", "-l"], user="root",
    )
    assert fd is not None
    try:
        sessions = session.active_sessions()
        assert len(sessions) == 1
        rec = sessions[0]
        assert rec["pid"] == os.getpid()
        assert rec["container"] == "ubuntu"
        assert rec["kind"] == "login"
        assert rec["command"] == ["/bin/bash", "-l"]
        assert rec["user"] == "root"
        assert "start_time" in rec
        assert os.path.exists(_session_path(os.getpid()))
    finally:
        # Closing the fd releases the inherited lock — the session is now
        # "dead" and must be pruned on the next listing.
        fd.close()

    assert session.active_sessions() == []
    assert not os.path.exists(_session_path(os.getpid()))


def test_register_is_inheritable():
    fd = session.register_session(
        container="x", kind="run", command_argv=["x"], user="root",
    )
    try:
        # O_CLOEXEC must be cleared so proot keeps the fd (and the lock)
        # across os.execvpe().
        assert os.get_inheritable(fd.fileno()) is True
    finally:
        fd.close()


def test_dead_session_file_is_pruned():
    os.makedirs(constants.SESSIONS_DIR, exist_ok=True)
    path = _session_path(999999)
    with open(path, "w") as fh:
        json.dump(
            {"pid": 999999, "container": "ghost", "kind": "login",
             "command": ["sh"], "user": "root", "start_time": 0.0},
            fh,
        )
    # No process holds the lock → treated as dead and removed.
    assert session.active_sessions() == []
    assert not os.path.exists(path)


def test_multiple_live_sessions_listed():
    fd1 = _make_live(111111, "alpha", start_time=1.0)
    fd2 = _make_live(222222, "beta", start_time=2.0)
    try:
        sessions = session.active_sessions()
        assert [s["container"] for s in sessions] == ["alpha", "beta"]
        assert {s["pid"] for s in sessions} == {111111, 222222}
    finally:
        fd1.close()
        fd2.close()
    # Both locks released → both pruned.
    assert session.active_sessions() == []


def test_live_and_dead_mixed():
    live = _make_live(123123, "live-one")
    dead_path = _session_path(456456)
    with open(dead_path, "w") as fh:
        json.dump(
            {"pid": 456456, "container": "dead-one", "kind": "run",
             "command": ["x"], "user": "root", "start_time": 0.0},
            fh,
        )
    try:
        sessions = session.active_sessions()
        assert [s["container"] for s in sessions] == ["live-one"]
        assert not os.path.exists(dead_path)        # dead pruned
        assert os.path.exists(_session_path(123123))  # live kept
    finally:
        live.close()


def test_active_sessions_missing_dir_returns_empty():
    # Nothing registered yet, directory may not exist.
    assert session.active_sessions() == []


def test_corrupt_session_file_does_not_crash():
    # A locked-but-unparseable file (e.g. mid-write on a non-atomic FS)
    # should be skipped, not raise.
    os.makedirs(constants.SESSIONS_DIR, exist_ok=True)
    fd = open(_session_path(778899), "w")
    fd.write("{ not json")
    fd.flush()
    fcntl.flock(fd.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        assert session.active_sessions() == []  # locked → kept on disk, skipped
    finally:
        fd.close()


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def test_fmt_uptime():
    assert _fmt_uptime(-5) == "0m00s"
    assert _fmt_uptime(0) == "0m00s"
    assert _fmt_uptime(44) == "0m44s"
    assert _fmt_uptime(65) == "1m05s"
    assert _fmt_uptime(192) == "3m12s"
    assert _fmt_uptime(3600) == "1h00m"
    assert _fmt_uptime(3864) == "1h04m"
    assert _fmt_uptime(90000) == "1d01h"


def test_fmt_command():
    assert _fmt_command(["nginx", "-g", "daemon off;"]) == "nginx -g 'daemon off;'"
    assert _fmt_command(["/bin/bash", "-l"]) == "/bin/bash -l"
    assert _fmt_command("plain string") == "plain string"
    assert _fmt_command(None) == ""
    assert _fmt_command([]) == ""


# ---------------------------------------------------------------------------
# command_ps dispatch
# ---------------------------------------------------------------------------

def test_command_ps_quiet_empty(capsys):
    command_ps(SimpleNamespace(quiet=True))
    assert capsys.readouterr().out == ""


def test_command_ps_quiet_lists_pids(capsys):
    fd1 = _make_live(313131, "a")
    fd2 = _make_live(323232, "b")
    try:
        command_ps(SimpleNamespace(quiet=True))
        out = capsys.readouterr().out.split()
        assert out == ["313131", "323232"]
    finally:
        fd1.close()
        fd2.close()


def test_command_ps_table_contains_fields(capsys):
    fd = _make_live(
        424242, "ubuntu", kind="login",
        command=["/bin/bash", "-l"], user="alice",
    )
    try:
        command_ps(SimpleNamespace(quiet=False))
        err = capsys.readouterr().err
        for token in ("PID", "CONTAINER", "COMMAND",
                      "424242", "ubuntu", "login", "alice", "/bin/bash"):
            assert token in err
    finally:
        fd.close()


def test_command_ps_table_empty(capsys):
    command_ps(SimpleNamespace(quiet=False))
    assert "No active sessions" in capsys.readouterr().err
