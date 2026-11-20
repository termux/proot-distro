# Terminal-escape containment for API events, beyond their messages.
#
# * An event's `resource` was never escaped, and it is often a name someone
#   else chose: backup names the guest's own file there when it leaves that
#   file's permissions alone. It is escaped by OperationEvent itself now,
#   spelled the way messages spell names (quote_path).
# * `search` runs in the caller's process rather than a worker, so its
#   retry notices skipped the escaping worker events get, and they quote
#   the HTTP error, reason phrase and all. The events this process makes
#   escape their message now too (printable_message).
# * Build output, a RUN step's own output, is still passed on as it came.

import os

import pytest

from proot_distro.api import EventKind, OperationEvent, ProotDistro
from proot_distro.api import program
from proot_distro.paths import container_rootfs

# An OSC that retitles the terminal, a C1 CSI, and a right-to-left override.
HOSTILE = "x\x1b]0;pwned\x07\x9b2J\u202ey"
QUOTED = "x\\e]0;pwned\\x07\\x9b2J\\u202ey"
CONTROLS = ("\x1b", "\x07", "\x9b", "\u202e")


def _assert_no_controls(text):
    for char in CONTROLS:
        assert char not in text, repr(text)


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root reads the file, so nothing is warned of")
def test_a_guest_chosen_name_is_escaped_in_an_event_resource(
        builders, tmp_path):
    builders.make_container("box")
    etc = os.path.join(container_rootfs("box"), "etc")
    os.makedirs(etc, exist_ok=True)
    planted = os.path.join(etc, HOSTILE)
    with open(planted, "w") as handle:
        handle.write("x")
    os.link(planted, os.path.join(etc, "other-link"))
    os.chmod(planted, 0)
    events = []
    try:
        ProotDistro(event_handler=events.append).backup(
            "box", tmp_path / "box.tar")
    finally:
        os.chmod(planted, 0o600)
    named = [event for event in events
             if event.resource and QUOTED in event.resource]
    assert named, [event.resource for event in events]
    for event in events:
        _assert_no_controls(event.message)
        _assert_no_controls(event.resource or "")


def test_a_search_retry_notice_is_escaped(monkeypatch):
    # The notice download.retry_http hands on_retry quotes the HTTP error,
    # whose reason phrase is the server's text.
    def search_images(query, limit, *, on_retry):
        on_retry(f"Searching: attempt 1/5 failed (HTTP Error 503: "
                 f"{HOSTILE}); retrying in 5s...")
        return [], 0

    monkeypatch.setattr(program, "search_images", search_images)
    events = []
    ProotDistro(event_handler=events.append).search("alpine")
    retries = [event for event in events if event.kind == EventKind.RETRY]
    assert len(retries) == 1
    _assert_no_controls(retries[0].message)
    assert QUOTED in retries[0].message


def test_every_event_resource_is_escaped_however_it_is_made():
    event = OperationEvent("backup", EventKind.WARNING, "m",
                           resource=HOSTILE)
    assert event.resource == QUOTED
    assert OperationEvent("backup", EventKind.INFO, "m").resource is None


def test_build_output_is_still_passed_on_as_it_came():
    # A RUN step's output is its own; colours and all, it is the caller's
    # to show or clean, and API.md says so.
    event = OperationEvent("build", EventKind.INFO, HOSTILE,
                           phase="build-output")
    assert event.message == HOSTILE
