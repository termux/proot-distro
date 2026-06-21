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
    _parse_signal,
    _read_pid_ppid,
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
