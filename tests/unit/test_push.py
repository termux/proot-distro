# Tests for proot_distro.helpers.docker.push — that registry uploads handle
# HTTP errors the same way as the install/pull path: transient failures (5xx)
# are retried via the shared retry_http policy, while deterministic ones (4xx)
# fail fast. The opener is mocked; no network.

import email.message
import hashlib

import urllib.error
import urllib.request

import pytest

from proot_distro.helpers import download
from proot_distro.helpers.docker import push


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    # Uploads share download.retry_http; neutralise its sleep so tests
    # exercising transient (retryable) failures don't actually wait.
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)


class _FakeResp:
    def __init__(self, status=200, headers=None):
        self.status = status
        self.headers = headers or {}

    def read(self, *a):
        return b""

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """Stand-in for transport.auth_opener()."""

    def __init__(self, fn):
        self._fn = fn

    def open(self, req, *a, **k):
        return self._fn(req, *a, **k)


def _patch_opener(monkeypatch, fn):
    monkeypatch.setattr(push, "opener", lambda insecure=False: _FakeOpener(fn))


_BASE = "https://reg.example"


def _http_error(url, code, reason):
    return urllib.error.HTTPError(url, code, reason, email.message.Message(),
                                  None)


# ----- _blob_exists -------------------------------------------------------

def test_blob_exists_true_on_200(monkeypatch):
    _patch_opener(monkeypatch, lambda req, *a, **k: _FakeResp(200))
    assert push._blob_exists("me/app", "sha256:aa", "TKN", _BASE) is True


def test_blob_exists_false_on_404_no_retry(monkeypatch):
    # A 404 means "blob absent" — deterministic, must not be retried.
    calls = []

    def fake(req, *a, **k):
        calls.append(req.full_url)
        raise _http_error(req.full_url, 404, "Not Found")

    _patch_opener(monkeypatch, fake)
    assert push._blob_exists("me/app", "sha256:aa", "TKN", _BASE) is False
    assert len(calls) == 1


def test_blob_exists_retries_transient(monkeypatch):
    # A 5xx is transient: retried (same policy as install) then succeeds.
    calls = []

    def fake(req, *a, **k):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise _http_error(req.full_url, 503, "Service Unavailable")
        return _FakeResp(200)

    _patch_opener(monkeypatch, fake)
    assert push._blob_exists("me/app", "sha256:aa", "TKN", _BASE) is True
    assert len(calls) == 3


def test_blob_exists_auth_error_propagates(monkeypatch):
    # 401 is deterministic: not retried, propagates for push_denied_msg.
    calls = []

    def fake(req, *a, **k):
        calls.append(req.full_url)
        raise _http_error(req.full_url, 401, "Unauthorized")

    _patch_opener(monkeypatch, fake)
    with pytest.raises(urllib.error.HTTPError) as exc:
        push._blob_exists("me/app", "sha256:aa", "TKN", _BASE)
    assert exc.value.code == 401
    assert len(calls) == 1


def test_blob_exists_uses_insecure_opener(monkeypatch):
    # The insecure flag must reach transport.opener so --allow-insecure pushes
    # over the unverified/HTTP opener, mirroring the install path.
    seen = {}

    def fake_opener(insecure=False):
        seen["insecure"] = insecure
        return _FakeOpener(lambda req, *a, **k: _FakeResp(200))

    monkeypatch.setattr(push, "opener", fake_opener)
    assert push._blob_exists(
        "me/app", "sha256:aa", "TKN", "http://reg.example", insecure=True
    ) is True
    assert seen["insecure"] is True


# ----- _put_manifest ------------------------------------------------------

def test_put_manifest_retries_then_returns_digest(monkeypatch):
    calls = []

    def fake(req, *a, **k):
        calls.append(req.full_url)
        if len(calls) < 2:
            raise _http_error(req.full_url, 500, "Internal Server Error")
        return _FakeResp(201, {"Docker-Content-Digest": "sha256:dd"})

    _patch_opener(monkeypatch, fake)
    digest = push._put_manifest(
        "me/app", "latest", b"{}",
        "application/vnd.oci.image.manifest.v1+json", "TKN", _BASE,
    )
    assert digest == "sha256:dd"
    assert len(calls) == 2


# ----- _upload_blob_bytes -------------------------------------------------

def test_upload_blob_bytes_retries_whole_session(monkeypatch):
    # A transient failure on the POST re-runs the whole upload session
    # (POST then monolithic PUT).
    methods = []

    def fake(req, *a, **k):
        methods.append(req.get_method())
        if req.get_method() == "POST":
            if methods.count("POST") == 1:
                raise _http_error(req.full_url, 503, "Service Unavailable")
            return _FakeResp(202, {"Location": "https://reg.example/upload/1"})
        return _FakeResp(201)  # PUT

    _patch_opener(monkeypatch, fake)
    push._upload_blob_bytes("me/app", "sha256:cc", b"data", "TKN", _BASE)
    assert methods == ["POST", "POST", "PUT"]


def test_upload_blob_bytes_4xx_fails_fast(monkeypatch):
    calls = []

    def fake(req, *a, **k):
        calls.append(req.get_method())
        raise _http_error(req.full_url, 403, "Forbidden")

    _patch_opener(monkeypatch, fake)
    with pytest.raises(urllib.error.HTTPError) as exc:
        push._upload_blob_bytes("me/app", "sha256:cc", b"data", "TKN", _BASE)
    assert exc.value.code == 403
    assert calls == ["POST"]  # deterministic -> no retry


# ----- push_image: --allow-insecure threading -----------------------------

def test_push_image_threads_base_and_insecure(monkeypatch, tmp_path):
    # push_image must resolve the scheme/base via get_auth_token(insecure=...)
    # and thread that base + the insecure flag into every upload helper, so an
    # HTTP-only (or bad-cert) registry is pushed to over the resolved transport.
    cfg_bytes = b"CFG"
    cfg_digest = "sha256:" + hashlib.sha256(cfg_bytes).hexdigest()
    layer_digest = "sha256:" + "a" * 64
    manifest = {
        "mediaType": "application/vnd.oci.image.manifest.v1+json",
        "config": {"digest": cfg_digest},
        "layers": [{"digest": layer_digest}],
    }

    blob = tmp_path / "layer"
    blob.write_bytes(b"data")

    monkeypatch.setattr(push, "load_manifest_cache",
                        lambda ref, arch: (manifest, "me/app", {"k": "v"}))
    monkeypatch.setattr(push, "canonical_json", lambda d: cfg_bytes)
    monkeypatch.setattr(push, "layer_cache_path", lambda d: str(blob))
    monkeypatch.setattr(push, "parse_image_ref",
                        lambda ref: ("reg.example", "me/app", "latest"))

    captured = {}

    def fake_auth(repo, registry, actions="pull", insecure=False):
        captured["auth_insecure"] = insecure
        # HTTP-only registry resolved to an http base under --allow-insecure.
        return "TKN", "http://reg.example"

    monkeypatch.setattr(push, "get_auth_token", fake_auth)

    def fake_blob_exists(repo, digest, token, base, insecure=False):
        captured["blob_base"] = base
        captured["blob_insecure"] = insecure
        return True  # everything already present -> skip uploads

    monkeypatch.setattr(push, "_blob_exists", fake_blob_exists)

    def fake_put_manifest(repo, ref, body, media, token, base, insecure=False):
        captured["manifest_base"] = base
        captured["manifest_insecure"] = insecure
        return "sha256:dd"

    monkeypatch.setattr(push, "_put_manifest", fake_put_manifest)

    result = push.push_image(
        "reg.example/me/app:latest", "x86_64", insecure=True
    )

    assert captured["auth_insecure"] is True
    assert captured["blob_base"] == "http://reg.example"
    assert captured["blob_insecure"] is True
    assert captured["manifest_base"] == "http://reg.example"
    assert captured["manifest_insecure"] is True
    assert result["manifest_digest"] == "sha256:dd"
