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
