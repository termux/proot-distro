# Terminal-escape containment for whole messages, not only the names in them.
#
# quote_path() kept C0 controls and DEL out of the names it quoted, but let
# C1 controls and format characters through: U+009B is CSI to a terminal
# just as ESC [ is, and an HTTP reason phrase is decoded as Latin-1, so a
# server's byte 0x9b arrives as exactly that; a right-to-left override
# makes text read as other text. And some messages were never quoted at
# all: the CLI printed "Network error: {exc}" with the reason phrase in it,
# and a busy lock's message named its holder's command line, read from a
# lock file that a container can write on Termux, raw in the CLI and in
# the API's BusyError.
#
# quote_path() escapes C1 controls and format characters now, the log
# helpers escape every message they print or hand on as an event, and the
# API escapes every error and event message a worker hands up.

import http.server
import os
import subprocess
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from proot_distro.api import BusyError, NetworkError, ProotDistro
from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.push import command_push
from proot_distro.commands.remove import command_remove
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import (
    OCI_MANIFEST_MEDIA, canonical_json,
)

_ROOT = Path(__file__).resolve().parents[2]

# A C1 CSI that clears the screen and a C1 NEL; both fit in Latin-1, which
# is all a status line can carry.
REASON = "\x9b2J\x85gone"
# For a lock file, which can hold anything: an OSC that retitles the
# terminal, the C1 CSI, and a right-to-left override.
HOLDER = "x\x1b]0;pwned\x07\x9b2J\u202ey"
CONTROLS = ("\x1b", "\x07", "\x85", "\x9b", "\u202e")


def _assert_no_controls(text):
    for char in CONTROLS:
        assert char not in text


class _Reason(http.server.BaseHTTPRequestHandler):
    """Answers everything 404, with a reason phrase of its own choosing."""

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(404, REASON)
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = do_PUT = do_POST = do_GET


@pytest.fixture
def reason_registry(monkeypatch, builders):
    """A cached image whose registry answers with a hostile reason."""
    monkeypatch.setenv("no_proxy", "*")
    server = http.server.HTTPServer(("127.0.0.1", 0), _Reason)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    ref = f"127.0.0.1:{server.server_port}/team/app:1"
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "a", "type": "file", "data": b"x"}])
    config = {"rootfs": {"type": "layers", "diff_ids": [diff_id]}}
    import hashlib
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA,
        "config": {"digest": "sha256:" + hashlib.sha256(
            canonical_json(config)).hexdigest()},
        "layers": [{"digest": digest, "size": size}],
    }
    save_manifest_cache(ref, get_device_cpu_arch(), manifest, "team/app",
                        config)
    yield ref
    server.shutdown()
    server.server_close()


def test_a_reason_phrase_never_reaches_the_cli_terminal(
        reason_registry, capsys):
    with pytest.raises(SystemExit):
        command_push(SimpleNamespace(
            image_ref=reason_registry, override_arch=None, quiet=False,
            allow_insecure=True,
        ))
    err = capsys.readouterr().err
    _assert_no_controls(err)
    assert "HTTP Error 404: \\x9b2J\\x85gone" in err


def test_a_reason_phrase_is_escaped_in_the_api_error(reason_registry):
    with pytest.raises(NetworkError) as exc:
        ProotDistro().push(reason_registry, allow_insecure=True)
    _assert_no_controls(str(exc.value))
    assert "\\x9b2J\\x85gone" in str(exc.value)


_HELPER = """
import sys
sys.path.insert(0, {root!r})
from proot_distro.locking import ContainerLock
lock = ContainerLock("box", exclusive=True, command={holder!r})
assert lock.acquire()
print("ready", flush=True)
sys.stdin.read()
"""


@pytest.fixture
def locked_box(builders):
    """Container 'box', locked by another process named HOLDER."""
    builders.make_container("box")
    helper = subprocess.Popen(
        [sys.executable, "-c",
         _HELPER.format(root=str(_ROOT), holder=HOLDER)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
        env=os.environ.copy(),
    )
    try:
        assert helper.stdout.readline().strip() == "ready"
        yield "box"
    finally:
        helper.stdin.close()
        helper.wait(10)


def test_a_lock_holders_command_never_reaches_the_cli_terminal(
        locked_box, capsys):
    with pytest.raises(SystemExit):
        command_remove(SimpleNamespace(
            target=locked_box, image=False, override_arch=None,
            verbose=False,
        ))
    err = capsys.readouterr().err
    _assert_no_controls(err)
    assert "x\\e]0;pwned\\x07\\x9b2J\\u202ey" in err


def test_a_lock_holders_command_is_escaped_in_the_api_error(locked_box):
    with pytest.raises(BusyError) as exc:
        ProotDistro().remove_container(locked_box)
    _assert_no_controls(str(exc.value))
    assert "x\\e]0;pwned\\x07\\x9b2J\\u202ey" in str(exc.value)
