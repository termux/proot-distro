# Containment tests for the session registry under RUNTIME_DIR/sessions.
#
# That directory is guest-writable — on Termux it sits under the
# $TERMUX_PREFIX bound read-write into every non-isolated container.
# os.makedirs(exist_ok=True) accepted a symlink in its place, so login
# wrote its JSON wherever the link led; and active_sessions() unlinks
# every *.json nobody holds a lock on, so a single `ps` emptied that
# directory of files ending in .json.

import os
import stat

import pytest

from proot_distro import session
from proot_distro.constants import SESSIONS_DIR


def _register(**kw):
    base = dict(container="box", kind="login", command_argv=["sh"],
                user="root")
    base.update(kw)
    return session.register_session(**base)


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    (d / "keep.json").write_text('{"important": true}\n')
    return d


def test_symlinked_registry_dir_is_refused(outside):
    os.makedirs(os.path.dirname(SESSIONS_DIR), exist_ok=True)
    os.symlink(str(outside), SESSIONS_DIR)

    fd = _register()
    try:
        assert fd is None
        assert sorted(os.listdir(str(outside))) == ["keep.json"]
    finally:
        if fd is not None:
            fd.close()


def test_ps_does_not_prune_through_a_symlinked_registry_dir(outside):
    os.makedirs(os.path.dirname(SESSIONS_DIR), exist_ok=True)
    os.symlink(str(outside), SESSIONS_DIR)

    assert session.active_sessions() == []
    assert (outside / "keep.json").exists()


def test_planted_entry_is_pruned_without_following_it(tmp_path):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    victim = tmp_path / "host.json"
    victim.write_text('{"pid": 1}\n')
    link = os.path.join(SESSIONS_DIR, "4242.json")
    os.symlink(str(victim), link)

    assert session.active_sessions() == []
    # The name went; what it pointed at did not.
    assert not os.path.lexists(link)
    assert victim.exists()


def test_fifo_entry_does_not_block_ps(tmp_path):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    os.mkfifo(os.path.join(SESSIONS_DIR, "4243.json"))

    # Opening a FIFO for reading succeeds even with no writer, so this
    # would only hang on a write — but it is not a session record either
    # way, and it must not be reported as one.
    assert session.active_sessions() == []


def test_registration_replaces_a_planted_final_name(tmp_path):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    victim = tmp_path / "host.json"
    victim.write_text("host content\n")
    os.symlink(str(victim), os.path.join(SESSIONS_DIR, f"{os.getpid()}.json"))

    fd = _register()
    try:
        assert fd is not None
        assert victim.read_text() == "host content\n"
        final = os.path.join(SESSIONS_DIR, f"{os.getpid()}.json")
        assert stat.S_ISREG(os.lstat(final).st_mode)
        assert session.active_sessions()[0]["container"] == "box"
    finally:
        if fd is not None:
            fd.close()


def test_session_holders_ignores_a_planted_entry(tmp_path):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    victim = tmp_path / "host.json"
    victim.write_text("x\n")
    os.symlink(str(victim), os.path.join(SESSIONS_DIR, "4244.json"))

    holder = open(str(victim))
    try:
        assert session.session_holders(4244) == set()
    finally:
        holder.close()


def test_session_is_live_ignores_a_planted_entry(tmp_path):
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    victim = tmp_path / "host.json"
    victim.write_text("x\n")
    os.symlink(str(victim), os.path.join(SESSIONS_DIR, "4245.json"))
    assert session.session_is_live(4245) is False


def test_sessions_parts_match_the_constant():
    from proot_distro.constants import RUNTIME_DIR
    assert os.path.join(RUNTIME_DIR, *session._SESSIONS_PARTS) == SESSIONS_DIR


# ---------------------------------------------------------------------------
# Forged records: a guest that can write here can compose one, and the
# file name is the only thing tying a record to a process.
# ---------------------------------------------------------------------------

import fcntl  # noqa: E402
import json  # noqa: E402

from proot_distro.commands import kill  # noqa: E402


def _plant(name, payload):
    """Write *payload* under *name* and hold its exclusive lock.

    That is all a guest has to do: the liveness probe is a shared flock,
    so a record nobody holds is pruned and one its author holds is
    "live". The returned handle must outlive the assertions.
    """
    os.makedirs(SESSIONS_DIR, exist_ok=True)
    fh = open(os.path.join(SESSIONS_DIR, name), "w")
    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    json.dump(payload, fh)
    fh.flush()
    return fh


def _record(pid, **over):
    base = {"pid": pid, "container": "box", "kind": "login",
            "command": ["/bin/sh", "-l"], "user": "root",
            "start_time": 1.0, "isolated": False, "minimal": False,
            "detach": False}
    base.update(over)
    return base


def test_a_record_under_a_name_that_is_not_a_pid_is_not_a_session():
    # `fake.json` claiming PID 4321 was probed for liveness under its own
    # name and then had 4321's *holders* looked up -- of which there are
    # none, because 4321 never registered -- so `kill` fell through to
    # "is 4321 a live proot?" and signalled an unrelated proot process.
    fh = _plant("fake.json", _record(4321))
    try:
        assert session.active_sessions() == []
    finally:
        fh.close()


@pytest.mark.parametrize("name", ["007.json", "0.json", "-1.json",
                                  "1e3.json", "١٢.json", "12 .json",
                                  "12.json.json"])
def test_only_the_canonical_decimal_name_registers_a_pid(name):
    assert session._record_pid(name) is None


def test_a_record_naming_another_pid_is_not_a_session():
    # The same forgery under a well-formed name: the file is 4321.json
    # but the payload names 9999, so kill would have gone looking for
    # 9999's holders while the liveness of 4321 said "alive".
    fh = _plant("4321.json", _record(9999))
    try:
        assert session.active_sessions() == []
    finally:
        fh.close()


@pytest.mark.parametrize("over", [
    {"container": "../../etc"},         # not a name this program accepts
    {"container": 5},
    {"kind": "sudo"},                   # outside the closed vocabulary
    {"kind": None},
    {"command": "rm -rf /"},            # not a list
    {"command": ["ok", 5]},
    {"user": 5},
    {"start_time": "soon"},             # TypeError out of the sort
    {"start_time": float("nan")},
    {"detach": "yes"},
    {"pid": True},
    {"pid": "4321"},
])
def test_a_malformed_field_makes_it_not_a_record(over):
    payload = _record(4321)
    payload.update(over)
    fh = _plant("4321.json", payload)
    try:
        assert session.active_sessions() == []
    finally:
        fh.close()


def test_a_wellformed_planted_record_still_describes_only_its_own_name():
    # What a guest can still do -- register its *own* PID -- is all it
    # can do: the record it composes is reported under the name it wrote,
    # and every consumer asks about that same name.
    fh = _plant("4321.json", _record(4321))
    try:
        sessions = session.active_sessions()
        assert [s["pid"] for s in sessions] == [4321]
        assert sessions[0]["container"] == "box"
    finally:
        fh.close()


def test_kill_does_not_signal_a_recorded_pid_whose_file_went_unheld(
    monkeypatch
):
    # The holder scan is what normally decides the target. When it comes
    # up empty -- /proc unreadable, or the session ended between the
    # listing and the scan -- the fallback signals the recorded PID
    # directly, and `_root_is_proot` says yes to *any* proot. Gating it
    # on the registry file still being locked is what keeps a recycled
    # PID out of the sweep.
    monkeypatch.setattr(kill, "_root_is_proot", lambda pid: True)
    monkeypatch.setattr(kill, "session_holders", lambda pid: set())
    monkeypatch.setattr(kill, "_is_alive", lambda pid: True)

    fh = _plant("4321.json", _record(4321))
    try:
        assert kill._session_roots(4321, {}) == [4321]
    finally:
        fh.close()

    # Same PID, same record — but nothing holds the file any more.
    assert kill._session_roots(4321, {}) == []
