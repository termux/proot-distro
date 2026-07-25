# Tests for proot_distro.commands.kill — the pure process-tree walk and
# signal parsing, plus an end-to-end teardown of a real process tree.
#
# The tree-walk helpers are pure given a {pid: ppid} map, so most cases
# need no real processes. The integration tests spawn a benign
# `sh`/`sleep` tree to exercise the live /proc reader and the full
# command_kill handler (with the proot-comm guard stubbed, since the test
# root is a shell, not proot).

import os
import shutil
import signal
import subprocess
import time

from types import SimpleNamespace

import pytest

from proot_distro.commands import kill
from proot_distro.commands.kill import (
    _collect_tree,
    _forest_roots,
    _is_alive,
    _is_pid_token,
    _parse_signal,
    _proot_comm_names,
    _read_pid_ppid,
    _root_is_proot,
    _signal_label,
    command_kill,
)

_HAVE_SH = shutil.which("sh") is not None and shutil.which("sleep") is not None


# ---------------------------------------------------------------------------
# _collect_tree (pure)
# ---------------------------------------------------------------------------

def test_collect_tree_linear_chain():
    # 1 -> 2 -> 3 -> 4
    m = {2: 1, 3: 2, 4: 3}
    assert _collect_tree(1, m) == {1, 2, 3, 4}
    assert _collect_tree(3, m) == {3, 4}


def test_collect_tree_branching():
    #        10
    #       /  \
    #     11    12
    #    /  \
    #  13    14
    m = {11: 10, 12: 10, 13: 11, 14: 11}
    assert _collect_tree(10, m) == {10, 11, 12, 13, 14}
    assert _collect_tree(11, m) == {11, 13, 14}


def test_collect_tree_root_without_children():
    assert _collect_tree(42, {1: 0, 2: 1}) == {42}


def test_collect_tree_self_and_cycle_safe():
    # Self-reference (5 -> 5) and a 2-cycle (7 -> 8 -> 7) must not loop.
    assert _collect_tree(5, {5: 5}) == {5}
    assert _collect_tree(7, {7: 8, 8: 7}) == {7, 8}


def test_collect_tree_empty_map():
    assert _collect_tree(999, {}) == {999}


# ---------------------------------------------------------------------------
# _parse_signal / _signal_label
# ---------------------------------------------------------------------------

def test_parse_signal_names():
    assert _parse_signal("TERM") == signal.SIGTERM
    assert _parse_signal("SIGTERM") == signal.SIGTERM
    assert _parse_signal("kill") == signal.SIGKILL
    assert _parse_signal("HUP") == signal.SIGHUP


def test_parse_signal_numbers():
    assert int(_parse_signal("9")) == 9
    assert int(_parse_signal("15")) == 15


def test_parse_signal_invalid_name_exits():
    with pytest.raises(SystemExit):
        _parse_signal("bogus")


def test_parse_signal_invalid_number_exits():
    with pytest.raises(SystemExit):
        _parse_signal("99999")


def test_signal_label():
    assert _signal_label(signal.SIGKILL) == "SIGKILL"
    assert _signal_label(signal.SIGTERM) == "SIGTERM"


# ---------------------------------------------------------------------------
# command_kill argument validation
# ---------------------------------------------------------------------------

def test_command_kill_no_target_no_all_exits():
    with pytest.raises(SystemExit):
        command_kill(SimpleNamespace(target=None, all=False, signal=None))


def test_command_kill_target_with_all_exits():
    with pytest.raises(SystemExit):
        command_kill(SimpleNamespace(target="box", all=True, signal=None))


def test_command_kill_unknown_pid_is_noop(monkeypatch):
    monkeypatch.setattr(kill, "active_sessions", lambda: [])
    # No matching session -> friendly message, no exception, no signals.
    command_kill(SimpleNamespace(target="424242", all=False, signal=None))


# ---------------------------------------------------------------------------
# Live process tree (needs sh + sleep)
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_SH, reason="needs sh and sleep binaries")
def test_read_pid_ppid_and_collect_live_tree():
    proc = subprocess.Popen(["sh", "-c", "sleep 300 & sleep 300 & wait"])
    tree = {proc.pid}
    try:
        time.sleep(0.3)  # allow the two children to spawn
        tree = _collect_tree(proc.pid, _read_pid_ppid())
        assert proc.pid in tree
        # Parent shell plus its two sleep children.
        assert len(tree) >= 3
    finally:
        for pid in sorted(tree, reverse=True):
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
        try:
            proc.wait(timeout=3)
        except Exception:
            pass


@pytest.mark.skipif(not _HAVE_SH, reason="needs sh and sleep binaries")
def test_command_kill_tears_down_whole_tree(monkeypatch):
    proc = subprocess.Popen(["sh", "-c", "sleep 300 & sleep 300 & wait"])
    time.sleep(0.3)

    # The test root is a shell, not proot, so bypass the comm guard.
    monkeypatch.setattr(kill, "_root_is_proot", lambda pid: True)
    monkeypatch.setattr(
        kill, "active_sessions",
        lambda: [{"pid": proc.pid, "container": "box", "kind": "run"}],
    )

    tree = _collect_tree(proc.pid, _read_pid_ppid())
    assert len(tree) >= 3

    command_kill(SimpleNamespace(target="box", all=False, signal=None))

    try:
        proc.wait(timeout=3)
    except Exception:
        pass
    time.sleep(0.2)

    survivors = []
    for pid in tree:
        try:
            os.kill(pid, 0)
            survivors.append(pid)
        except ProcessLookupError:
            pass
        except PermissionError:
            survivors.append(pid)
    assert survivors == []


# ---------------------------------------------------------------------------
# _forest_roots (pure) — topmost members of a set of session processes
# ---------------------------------------------------------------------------

def test_forest_roots_picks_only_the_topmost_members():
    #  10 -> 11 -> 13, and a detached 20
    m = {11: 10, 13: 11, 20: 1, 10: 5}
    assert _forest_roots({10, 11, 13}, m) == [10]
    # A guest that double-forked away from proot is reparented to init,
    # so it is a root of its own even though it is the same session.
    assert _forest_roots({10, 11, 13, 20}, m) == [10, 20]


def test_forest_roots_treats_a_vanished_parent_as_absent():
    # 11's parent is not in the map at all (already reaped).
    assert _forest_roots({11, 12}, {12: 11}) == [11]


# ---------------------------------------------------------------------------
# _is_pid_token / target parsing
# ---------------------------------------------------------------------------

def test_is_pid_token_accepts_only_ascii_decimals():
    assert _is_pid_token("12345")
    assert not _is_pid_token("box")
    # str.isdigit() is True for these but int() rejects them.
    assert not _is_pid_token("²")      # superscript two
    assert not _is_pid_token("①")      # circled digit one
    assert not _is_pid_token("٣")      # arabic-indic three


def test_command_kill_unicode_digit_target_reports_cleanly(monkeypatch):
    monkeypatch.setattr(kill, "active_sessions", lambda: [])
    # Must be a clean "invalid name" exit, never an unhandled ValueError.
    with pytest.raises(SystemExit):
        command_kill(SimpleNamespace(target="²", all=False, signal=None))


# ---------------------------------------------------------------------------
# _is_alive — zombies are not survivors
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_SH, reason="needs sh binary")
def test_is_alive_reports_unreaped_zombie_as_dead():
    proc = subprocess.Popen(["sh", "-c", "exit 0"])
    try:
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            with open(f"/proc/{proc.pid}/stat") as fh:
                if fh.read().rsplit(")", 1)[1].split()[0] == "Z":
                    break
            time.sleep(0.02)
        # Deliberately not reaped yet: os.kill(pid, 0) would still succeed.
        os.kill(proc.pid, 0)
        assert _is_alive(proc.pid) is False
    finally:
        proc.wait()


def test_is_alive_false_for_missing_process():
    assert _is_alive(2 ** 30) is False


# ---------------------------------------------------------------------------
# Container-root identification (PD_PROOT_BIN may rename the binary)
# ---------------------------------------------------------------------------

def test_proot_comm_names_defaults_to_proot(monkeypatch):
    monkeypatch.delenv("PD_PROOT_BIN", raising=False)
    assert _proot_comm_names() == {"proot"}


def test_proot_comm_names_honours_pd_proot_bin(monkeypatch):
    monkeypatch.setenv("PD_PROOT_BIN", "/opt/pd/proot-static")
    assert _proot_comm_names() == {"proot", "proot-static"}


def test_proot_comm_names_truncated_like_proc_comm(monkeypatch):
    # /proc/<pid>/comm holds at most 15 characters.
    monkeypatch.setenv("PD_PROOT_BIN", "/opt/pd/proot-with-a-very-long-name")
    assert "proot-with-a-ve" in _proot_comm_names()


@pytest.mark.skipif(not _HAVE_SH, reason="needs sh binary")
def test_root_is_proot_accepts_renamed_binary(monkeypatch, tmp_path):
    # A session started through PD_PROOT_BIN must not be refused just
    # because its comm is not the literal "proot".
    alt = tmp_path / "proot-static"
    shutil.copy(shutil.which("sh"), alt)
    alt.chmod(0o755)
    proc = subprocess.Popen([str(alt), "-c", "sleep 30"])
    try:
        time.sleep(0.3)
        monkeypatch.delenv("PD_PROOT_BIN", raising=False)
        assert _root_is_proot(proc.pid) is False
        monkeypatch.setenv("PD_PROOT_BIN", str(alt))
        assert _root_is_proot(proc.pid) is True
    finally:
        proc.kill()
        proc.wait()


def test_root_is_proot_none_for_missing_process():
    assert _root_is_proot(2 ** 30) is None


# ---------------------------------------------------------------------------
# Teardown escalation policy
# ---------------------------------------------------------------------------

def test_teardown_escalates_when_the_session_outlives_the_signal(monkeypatch):
    # proot sets SIG_IGN on SIGTERM, so a session that is still up after
    # the grace period must be taken down with SIGQUIT, then swept.
    monkeypatch.setattr(kill, "_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(kill, "_live_roots", lambda t, p=None: [42])
    monkeypatch.setattr(kill, "_drained", lambda t, p=None: False)
    sent, escalated = [], []
    monkeypatch.setattr(kill, "_signal_pass",
                        lambda roots, sig: sent.append(sig) or 1)
    monkeypatch.setattr(kill, "_escalate",
                        lambda roots: escalated.append(roots) or 1)

    kill._teardown([{"pid": 42}], signal.SIGTERM)

    assert escalated == [[42]]
    assert sent == [signal.SIGTERM, signal.SIGKILL]


def test_teardown_stops_at_the_requested_signal_once_drained(monkeypatch):
    monkeypatch.setattr(kill, "_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(kill, "_live_roots", lambda t, p=None: [42])
    monkeypatch.setattr(kill, "_drained", lambda t, p=None: True)
    sent, escalated = [], []
    monkeypatch.setattr(kill, "_signal_pass",
                        lambda roots, sig: sent.append(sig) or 1)
    monkeypatch.setattr(kill, "_escalate",
                        lambda roots: escalated.append(roots) or 1)

    kill._teardown([{"pid": 42}], signal.SIGTERM)

    assert escalated == []
    assert sent == [signal.SIGTERM]


@pytest.mark.parametrize("signame", ["SIGSTOP", "SIGCONT", "SIGTSTP",
                                     "SIGUSR1", "SIGUSR2"])
def test_teardown_never_escalates_non_terminating(monkeypatch, signame):
    # `kill -s STOP` must suspend, not terminate.
    monkeypatch.setattr(kill, "_GRACE_SECONDS", 0.05)
    monkeypatch.setattr(kill, "_live_roots", lambda t, p=None: [42])
    monkeypatch.setattr(kill, "_drained", lambda t, p=None: False)
    sent, escalated = [], []
    monkeypatch.setattr(kill, "_signal_pass",
                        lambda roots, sig: sent.append(sig) or 1)
    monkeypatch.setattr(kill, "_escalate",
                        lambda roots: escalated.append(roots) or 1)

    kill._teardown([{"pid": 42}], getattr(signal, signame))

    assert escalated == []
    assert sent == [getattr(signal, signame)]


def test_escalate_sends_sigquit_only_to_proot_roots(monkeypatch):
    # An orphaned guest is not proot: SIGQUIT would only dump core, so
    # it is left to the SIGKILL sweep.
    monkeypatch.setattr(kill, "_is_alive", lambda pid: True)
    monkeypatch.setattr(kill, "_root_is_proot", lambda pid: pid == 10)
    signalled = []
    monkeypatch.setattr(kill.os, "kill",
                        lambda pid, sig: signalled.append((pid, sig)))

    assert kill._escalate([10, 20]) == 1
    assert signalled == [(10, signal.SIGQUIT)]


# ---------------------------------------------------------------------------
# Truthful reporting
# ---------------------------------------------------------------------------

def test_report_exits_nonzero_when_processes_survive(monkeypatch):
    monkeypatch.setattr(kill, "_survivors", lambda tracked: [111, 222])
    with pytest.raises(SystemExit) as excinfo:
        kill._report([{"pid": 111}], signal.SIGTERM, 4)
    assert excinfo.value.code == 1


def test_report_succeeds_when_nothing_survives(monkeypatch):
    monkeypatch.setattr(kill, "_survivors", lambda tracked: [])
    kill._report([{"pid": 111}], signal.SIGTERM, 4)  # must not raise


def test_report_skips_survivor_check_for_non_terminating_signals(monkeypatch):
    checked = []
    monkeypatch.setattr(kill, "_survivors",
                        lambda tracked: checked.append(1) or [])
    kill._report([{"pid": 111}], signal.SIGSTOP, 3)
    assert checked == []


# ---------------------------------------------------------------------------
# Live teardown of a tree that ignores the requested signal
# ---------------------------------------------------------------------------

@pytest.mark.skipif(not _HAVE_SH, reason="needs sh and sleep binaries")
def test_command_kill_tears_down_tree_that_ignores_term_and_quit(monkeypatch):
    # Stands in for the real failure: proot ignores SIGTERM outright and
    # an interactive guest shell does too, so the session used to survive
    # `kill` entirely. The sweep must finish the job.
    proc = subprocess.Popen(
        ["sh", "-c", 'trap "" TERM QUIT; sleep 300 & sleep 300 & wait'])
    time.sleep(0.3)

    monkeypatch.setattr(kill, "_GRACE_SECONDS", 0.2)
    monkeypatch.setattr(kill, "_root_is_proot", lambda pid: True)
    monkeypatch.setattr(
        kill, "active_sessions",
        lambda: [{"pid": proc.pid, "container": "box", "kind": "login"}],
    )

    tree = _collect_tree(proc.pid, _read_pid_ppid())
    assert len(tree) >= 3

    command_kill(SimpleNamespace(target="box", all=False, signal=None))

    try:
        proc.wait(timeout=3)
    except Exception:
        pass
    time.sleep(0.2)

    survivors = [pid for pid in tree if _is_alive(pid)]
    assert survivors == []
