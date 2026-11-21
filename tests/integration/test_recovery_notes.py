# What a failed reset or restore cost reaches every caller, not only the
# ones reading events.
#
# A reset, and a restore over a container, remove the old container before
# they write the new one (container_staging.clear_container), so one that
# fails loses it. The CLI said so, and how to get it back, as an error
# line; the API sent the same line as an event and raised an exception
# that never mentioned it -- a NetworkError, or an OperationCancelledError
# for a cancel -- so a caller with no event handler did not learn that its
# container was gone. operation_errors.attach_recovery() has the exception
# carry the note, the API worker appends it to the message of the error it
# raises, and hands it to the parent for the message of a cancellation.

import http.server
import json
import threading

import pytest

from proot_distro.api import OperationCancelledError, ProotDistro
from proot_distro.api import program
from proot_distro.constants import PROGRAM_NAME
from proot_distro.paths import container_entry_lstat

NOTE = "was removed, but its new copy was not installed. Install it again"


class _PlainHttp(http.server.BaseHTTPRequestHandler):
    """A registry that speaks plain HTTP: reset refuses it at once."""

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(404)
        self.send_header("Content-Length", "0")
        self.end_headers()


@pytest.fixture
def plain_registry():
    server = http.server.HTTPServer(("127.0.0.1", 0), _PlainHttp)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{server.server_port}"
    server.shutdown()
    server.server_close()


def test_a_failed_reset_says_in_its_exception_how_to_get_it_back(
        builders, plain_registry):
    """Regression: only an event said the container was gone."""
    ref = f"{plain_registry}/team/app:1"
    builders.make_container("box", manifest={
        "image_ref": ref, "arch": "x86_64",
        "manifest": {"schemaVersion": 2, "layers": []},
        "image_config": {"config": {}},
    })

    with pytest.raises(Exception) as exc:
        ProotDistro().reset_container("box")

    assert container_entry_lstat("box") is None
    assert f"Container 'box' {NOTE}" in str(exc.value)
    assert (f"'{PROGRAM_NAME} install --name box --architecture x86_64 "
            f"{ref}'") in str(exc.value)


def test_a_cancelled_reset_says_in_its_exception_how_to_get_it_back(
        builders):
    from proot_distro.api import EventKind
    from proot_distro.helpers.docker.cache import save_manifest_cache
    from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA

    members = [{"name": f"var/f{i:04d}", "type": "file", "data": b"x" * 64}
               for i in range(1500)]
    digest, size, diff_id = builders.seed_cached_layer(members)
    manifest = {"schemaVersion": 2,
                "layers": [{"digest": digest, "size": size,
                            "mediaType": OCI_LAYER_MEDIA}]}
    save_manifest_cache("img:1", "x86_64", manifest, "library/img",
                        {"config": {}, "rootfs": {"diff_ids": [diff_id]}})
    builders.make_container("box", manifest={
        "image_ref": "img:1", "arch": "x86_64",
        "manifest": manifest, "image_config": {"config": {}},
    })
    cancelled = threading.Event()

    def cancel_once_the_old_one_is_gone(event):
        if (event.kind == EventKind.PROGRESS
                and container_entry_lstat("box") is None):
            cancelled.set()

    with pytest.raises(OperationCancelledError) as exc:
        ProotDistro(event_handler=cancel_once_the_old_one_is_gone) \
            .reset_container("box", cancel_event=cancelled)

    assert container_entry_lstat("box") is None
    message = str(exc.value)
    assert message.startswith("reset_container was cancelled. ")
    assert f"Container 'box' {NOTE}" in message


@pytest.mark.parametrize("stdout, note", [
    (json.dumps({"_error": {"kind": "cancelled", "message": "Gone."}}),
     " Gone."),
    (json.dumps({"_error": {"kind": "operation", "message": "x"}}), ""),
    (json.dumps({"_error": {"kind": "cancelled", "message": 5}}), ""),
    ("not json", ""),
    ("", ""),
])
def test_only_a_cancellation_note_is_added_to_a_cancel(stdout, note):
    assert program._cancellation_note((stdout.encode(), b"")) == note
    assert program._cancellation_note(None) == ""


_WORKER_PROBE = """
import json, sys
sys.path.insert(0, {root!r})
from proot_distro import _api_worker
from proot_distro.operation_errors import NetworkFailure, attach_recovery

def failing(_options):
    exc = NetworkFailure("the registry cannot be reached.")
    attach_recovery(exc, "Gone; reinstall it.")
    raise exc

written = []
_api_worker._OPERATIONS["probe"] = failing
_api_worker._read_request = lambda: ("probe", {{}})
_api_worker._write_result = written.append
code = _api_worker.main()
print(json.dumps([code, written]))
"""


def test_the_worker_appends_the_note_to_any_failure():
    # In a fresh interpreter: the worker's main() sets the umask, quiet
    # mode and the lock behaviour of the process it runs in, for good.
    import os
    import subprocess
    import sys
    from pathlib import Path

    root = str(Path(__file__).resolve().parents[2])
    done = subprocess.run(
        [sys.executable, "-c", _WORKER_PROBE.format(root=root)],
        capture_output=True, text=True, env=os.environ.copy(), timeout=60,
    )
    code, written = json.loads(done.stdout.strip().splitlines()[-1])
    assert code == 1
    assert written == [{"_error": {
        "kind": "network",
        "message": "the registry cannot be reached. Gone; reinstall it.",
    }}]
