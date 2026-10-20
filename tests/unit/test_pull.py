# Tests for proot_distro.helpers.docker.pull — the framing of the two
# metadata bodies it reads whole. A Content-Length body cut short raises
# nothing on its own (CPython's HTTPResponse.read(amt) declines to), so
# the short bytes were simply what the caller got: a manifest fetched by
# tag has no digest to catch it, and a config blob's digest mismatch was
# reported as the registry serving the wrong bytes -- fatal, not retried.
# The opener is mocked; no network.

import email.message
import hashlib
import io
import json
import os
import urllib.error
from types import SimpleNamespace

import pytest

from proot_distro.helpers import download
from proot_distro.helpers.docker import layers as layers_mod
from proot_distro.helpers.docker import pull as pull_mod
from proot_distro.helpers.docker.transport import auth_denied_msg


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


# ----- Docker Hub is always verified --------------------------------------

@pytest.mark.parametrize("ref,expected", [
    ("alpine", False),                     # Docker Hub
    ("docker.io/library/alpine", False),   # its aliases parse to the same
    ("index.docker.io/library/alpine", False),
    ("reg.example/alpine", True),
    ("localhost:5000/alpine", True),
])
def test_pull_image_keeps_docker_hub_verified(monkeypatch, ref, expected):
    # --allow-insecure used to reach Hub's manifests and blobs: the tag's
    # manifest, which no digest pins, was fetched with certificate checks
    # off and the Bearer token on it. Only the token exchange was always
    # verified. Decided once, where the pull starts.
    seen = []

    def fake_layers(image_ref, rootfs_fd, arch, insecure, *rest):
        seen.append(insecure)
        return {}

    monkeypatch.setattr(pull_mod, "_pull_layers", fake_layers)
    pull_mod.pull_image(ref, -1, "x86_64", insecure=True)
    pull_mod.pull_image(ref, -1, "x86_64", insecure=False)
    assert seen == [expected, False]


# ----- 429 Too Many Requests ----------------------------------------------

_N8N = "docker.n8n.io/n8nio/n8n:latest"

_LAYER = {
    "mediaType": "application/vnd.oci.image.layer.v1.tar+gzip",
    "digest": "sha256:" + "b" * 64,
    "size": 10,
}
_ONE_LAYER = {"schemaVersion": 2, "layers": [_LAYER]}


def _hub_quota_spent(url):
    # What docker.n8n.io answers any client that is not Docker itself.
    import email.message
    import urllib.error
    hdrs = email.message.Message()
    hdrs["Docker-RateLimit-Source"] = "192.0.2.10"
    hdrs["RateLimit-Remaining"] = "0;w=3600"
    hdrs["X-RateLimit-Remaining"] = "0;w=3600"
    return urllib.error.HTTPError(
        url, 429, "Too Many Requests", hdrs, io.BytesIO(b""),
    )


def _assert_points_at_docker_hub(text):
    assert "HTTP 429" in text
    assert "install it from there: 'n8nio/n8n:latest'" in text
    assert "Network error" not in text


def test_a_spent_quota_on_the_manifest_fails_at_once_and_says_why(
        monkeypatch):
    # Regression: `install docker.n8n.io/n8nio/n8n:latest` retried the
    # manifest four times over twenty seconds, then failed with "Network
    # error: HTTP Error 429: Too Many Requests".
    calls = []

    def _open(req):
        calls.append(req.full_url)
        raise _hub_quota_spent(req.full_url)

    monkeypatch.setattr(
        pull_mod, "get_auth_token",
        lambda *a, **k: ("T", "https://docker.n8n.io"),
    )
    monkeypatch.setattr(
        pull_mod, "opener",
        lambda insecure=False: SimpleNamespace(open=_open),
    )
    with pytest.raises(RuntimeError) as exc:
        pull_mod.pull_image(_N8N, -1, "x86_64")
    _assert_points_at_docker_hub(str(exc.value))
    assert calls == [
        "https://docker.n8n.io/v2/n8nio/n8n/manifests/latest",
    ]


def test_a_spent_quota_on_the_token_for_a_cached_manifest_says_why(
        monkeypatch):
    def _refuse(*a, **k):
        raise _hub_quota_spent("https://docker.n8n.io/v2/")

    monkeypatch.setattr(
        pull_mod, "load_manifest_cache",
        lambda *a: (_ONE_LAYER, "n8nio/n8n", {}),
    )
    monkeypatch.setattr(pull_mod, "annotate_manifest_cache", lambda *a: None)
    monkeypatch.setattr(pull_mod, "get_auth_token", _refuse)
    with pytest.raises(RuntimeError) as exc:
        pull_mod.pull_image(_N8N, -1, "x86_64")
    _assert_points_at_docker_hub(str(exc.value))


def test_a_spent_quota_on_a_layer_says_why(monkeypatch):
    def _refuse(repo, digest, token, base, insecure):
        raise _hub_quota_spent(f"{base}/v2/{repo}/blobs/{digest}")

    monkeypatch.setattr(
        pull_mod, "_resolve_single_manifest",
        lambda *a: (_ONE_LAYER, "T", "https://docker.n8n.io", {}),
    )
    monkeypatch.setattr(pull_mod, "_fetch_config_blob", lambda *a: {})
    monkeypatch.setattr(pull_mod, "save_manifest_cache", lambda *a, **k: None)
    monkeypatch.setattr(pull_mod, "download_blob", _refuse)
    with pytest.raises(RuntimeError) as exc:
        pull_mod.pull_image(_N8N, -1, "x86_64")
    _assert_points_at_docker_hub(str(exc.value))


# ----- authorization -------------------------------------------------------

@pytest.mark.parametrize("auth", [
    "Bearer T",                         # a token service's grant
    "Basic YWxpY2U6c2VjcmV0",           # a registry checking passwords itself
    "",                                 # an open registry
])
def test_every_pull_request_carries_the_resolved_authorization(monkeypatch,
                                                               auth):
    # get_auth_token resolves the whole header value, and the manifest,
    # the config and the layer are each asked for with exactly that.
    blob = b"blob bytes"
    digest = "sha256:" + hashlib.sha256(blob).hexdigest()
    sent = []

    def _open(req):
        sent.append(req.get_header("Authorization"))
        return _Resp(_MANIFEST if "/manifests/" in req.full_url else blob)

    fake = lambda insecure=False: SimpleNamespace(open=_open)  # noqa: E731
    monkeypatch.setattr(pull_mod, "opener", fake)
    monkeypatch.setattr(layers_mod, "opener", fake)
    pull_mod._get_manifest("team/app", "1", auth, "https://r")
    pull_mod._download_config("team/app", digest, auth, "https://r")
    os.close(layers_mod.download_blob("team/app", digest, auth, "https://r"))
    assert sent == [auth or None] * 3


_REF = "reg.example/team/app:1"


def _refusal(code):
    def _refuse(*a, **k):
        raise urllib.error.HTTPError(
            "https://reg.example/v2/", code, "Refused",
            email.message.Message(), io.BytesIO(b""),
        )
    return _refuse


def _refuse_at(monkeypatch, stage, code):
    """Make the pull of _REF meet HTTP *code* at *stage*."""
    refuse = _refusal(code)
    if stage == "token for a cached manifest":
        monkeypatch.setattr(pull_mod, "load_manifest_cache",
                            lambda *a: (_ONE_LAYER, "team/app", {}))
        monkeypatch.setattr(pull_mod, "annotate_manifest_cache",
                            lambda *a: None)
        monkeypatch.setattr(pull_mod, "get_auth_token", refuse)
    elif stage == "token":
        monkeypatch.setattr(pull_mod, "get_auth_token", refuse)
    elif stage == "manifest":
        monkeypatch.setattr(
            pull_mod, "get_auth_token",
            lambda *a, **k: ("Bearer T", "https://reg.example"),
        )
        monkeypatch.setattr(
            pull_mod, "opener",
            lambda insecure=False: SimpleNamespace(open=refuse),
        )
    else:
        monkeypatch.setattr(
            pull_mod, "_resolve_single_manifest",
            lambda *a: (_ONE_LAYER, "Bearer T", "https://reg.example", {}),
        )
        monkeypatch.setattr(pull_mod, "_fetch_config_blob", lambda *a: {})
        monkeypatch.setattr(pull_mod, "save_manifest_cache",
                            lambda *a, **k: None)
        monkeypatch.setattr(pull_mod, "download_blob", refuse)


@pytest.mark.parametrize("with_credentials", [False, True])
@pytest.mark.parametrize("code", [401, 403])
@pytest.mark.parametrize("stage", [
    "token for a cached manifest", "token", "manifest", "layer",
])
def test_a_refusal_at_every_stage_of_a_pull_says_access_denied(
        monkeypatch, stage, code, with_credentials):
    # The three places the pull reads a registry's 401/403 -- the token
    # for a cached manifest's missing layers, the manifest (and the token
    # before it), and a layer -- each have to say what auth_denied_msg
    # says, not "Network error: HTTP Error 401".
    if with_credentials:
        monkeypatch.setenv("PD_DOCKER_AUTH", "alice:secret")
    _refuse_at(monkeypatch, stage, code)
    with pytest.raises(RuntimeError) as exc:
        pull_mod.pull_image(_REF, -1, "x86_64")
    assert str(exc.value) == auth_denied_msg(_REF, code)
