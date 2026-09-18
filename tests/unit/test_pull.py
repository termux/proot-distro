# Tests for proot_distro.helpers.docker.pull — the framing of the two
# metadata bodies it reads whole. A Content-Length body cut short raises
# nothing on its own (CPython's HTTPResponse.read(amt) declines to), so
# the short bytes were simply what the caller got: a manifest fetched by
# tag has no digest to catch it, and a config blob's digest mismatch was
# reported as the registry serving the wrong bytes -- fatal, not retried.
# The opener is mocked; no network.

import hashlib
import io
import json
from types import SimpleNamespace

import pytest

from proot_distro.helpers import download
from proot_distro.helpers.docker import pull as pull_mod


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)


class _Resp(io.BytesIO):
    def __init__(self, body, declared=None):
        super().__init__(body)
        self.headers = {
            "Content-Type": "application/vnd.oci.image.manifest.v1+json",
        }
        if declared is not None:
            self.headers["Content-Length"] = str(declared)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _serve(monkeypatch, body, declared=None):
    calls = []

    def _open(req):
        calls.append(req.full_url)
        return _Resp(body, declared)

    monkeypatch.setattr(
        pull_mod, "opener",
        lambda insecure=False: SimpleNamespace(open=_open),
    )
    return calls


_MANIFEST = json.dumps({"schemaVersion": 2, "layers": []}).encode()


def test_a_manifest_short_of_its_declared_length_is_refused(monkeypatch):
    # Valid JSON, and the registry said there was more of it. It used to
    # be accepted as the whole manifest.
    calls = _serve(monkeypatch, _MANIFEST, declared=9999)
    with pytest.raises(download.IncompleteResponse):
        pull_mod._get_manifest("library/x", "latest", "T", "https://r")
    # Retried like any other connection that ended early, then reported.
    assert len(calls) == 5


def test_a_manifest_with_its_declared_length_is_read(monkeypatch):
    _serve(monkeypatch, _MANIFEST, declared=len(_MANIFEST))
    data = pull_mod._get_manifest("library/x", "latest", "T", "https://r")
    assert data["schemaVersion"] == 2


def test_a_manifest_declaring_no_length_is_read(monkeypatch):
    # Chunked, or a server that sends none: nothing to check against.
    _serve(monkeypatch, _MANIFEST)
    data = pull_mod._get_manifest("library/x", "latest", "T", "https://r")
    assert data["schemaVersion"] == 2


def test_the_size_refusal_wins_over_the_length_check(monkeypatch):
    # A body past the cap is short of its own length by construction; the
    # answer is "larger than", not "ended early", and it is not retried.
    monkeypatch.setattr(pull_mod, "MAX_METADATA_BYTES", 16)
    body = b"{" + b" " * 64 + b"}"
    calls = _serve(monkeypatch, body, declared=len(body))
    with pytest.raises(RuntimeError, match="larger than"):
        pull_mod._get_manifest("library/x", "latest", "T", "https://r")
    assert len(calls) == 1


def test_a_config_blob_short_of_its_length_is_retried_not_a_mismatch(
    monkeypatch,
):
    # The check comes before the digest, as in the layer download: a cut
    # connection is a network failure (retried, then the degraded {}
    # every other network failure here answers), not the registry
    # serving the wrong bytes, which is fatal.
    body = json.dumps({"architecture": "amd64"}).encode()
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    calls = _serve(monkeypatch, body, declared=len(body) + 100)
    assert pull_mod._fetch_config_blob("library/x", digest, "T",
                                       "https://r") == {}
    assert len(calls) == 5


def test_a_complete_config_blob_is_verified_and_read(monkeypatch):
    body = json.dumps({"architecture": "amd64"}).encode()
    digest = "sha256:" + hashlib.sha256(body).hexdigest()
    _serve(monkeypatch, body, declared=len(body))
    assert pull_mod._fetch_config_blob(
        "library/x", digest, "T", "https://r") == {"architecture": "amd64"}
