# Tests for proot_distro.helpers.docker.transport — auth challenge parsing,
# PD_DOCKER_AUTH handling, cross-host Authorization stripping, and the token
# exchange (with urlopen mocked; no network).

import base64
import email.message
import io
import json
import ssl

import urllib.error
import urllib.request

import pytest

from proot_distro.helpers.docker import transport


# ----- bearer challenge parsing -------------------------------------------

def test_parse_bearer_challenge_quoted():
    hdr = 'realm="https://auth.example/token",service="reg.example",scope="x"'
    params = transport._parse_bearer_challenge(hdr)
    assert params["realm"] == "https://auth.example/token"
    assert params["service"] == "reg.example"
    assert params["scope"] == "x"


def test_parse_bearer_challenge_bare_tokens():
    hdr = "realm=https://auth.example/token,service=svc"
    params = transport._parse_bearer_challenge(hdr)
    assert params["realm"] == "https://auth.example/token"
    assert params["service"] == "svc"


# ----- env_basic_auth -----------------------------------------------------

def test_env_basic_auth_unset(monkeypatch):
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    assert transport.env_basic_auth() == ""


def test_env_basic_auth_encodes(monkeypatch):
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:secret")
    expected = "Basic " + base64.b64encode(b"alice:secret").decode()
    assert transport.env_basic_auth() == expected


def test_env_basic_auth_requires_colon(monkeypatch):
    monkeypatch.setenv("PD_DOCKER_AUTH", "bare-token-no-colon")
    with pytest.raises(RuntimeError):
        transport.env_basic_auth()


# ----- cross-host Authorization stripping ---------------------------------

def _make_redirect(orig_url, new_url):
    handler = transport.AuthStrippingRedirectHandler()
    req = urllib.request.Request(
        orig_url,
        headers={"Authorization": "Bearer TOKEN", "User-Agent": "pd"},
    )
    resp_headers = email.message.Message()
    return handler.redirect_request(req, None, 302, "Found", resp_headers,
                                    new_url)


def test_redirect_strips_auth_cross_host():
    new_req = _make_redirect(
        "https://registry-1.docker.io/v2/foo/blobs/sha256:aa",
        "https://cdn.example.net/blob/abc",
    )
    assert new_req is not None
    assert new_req.get_header("Authorization") is None


def test_redirect_keeps_auth_same_host():
    new_req = _make_redirect(
        "https://registry-1.docker.io/v2/foo/blobs/sha256:aa",
        "https://registry-1.docker.io/v2/foo/blobs/redirected",
    )
    assert new_req is not None
    assert new_req.get_header("Authorization") == "Bearer TOKEN"


# ----- token exchange (mocked) --------------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def read(self, *a):
        return self._buf.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """Stand-in for the object returned by transport.opener(insecure)."""

    def __init__(self, fn):
        self._fn = fn

    def open(self, req, *a, **k):
        return self._fn(req, *a, **k)


def _patch_opener(monkeypatch, fn):
    monkeypatch.setattr(
        transport, "opener", lambda insecure=False: _FakeOpener(fn)
    )


def _ssl_error(reason: str) -> ssl.SSLError:
    err = ssl.SSLError(1, f"[SSL: {reason}] {reason.lower()}")
    err.reason = reason
    return err


def test_get_auth_token_docker_hub(monkeypatch):
    captured = {}

    def fake_urlopen(req, *a, **k):
        captured["url"] = req.full_url
        return _FakeResp({"token": "DOCKERHUB_TKN"})

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok, base = transport.get_auth_token("library/ubuntu")
    assert tok == "DOCKERHUB_TKN"
    assert base == transport.REGISTRY_URL
    assert "scope=repository:library/ubuntu:pull" in captured["url"]


def test_get_auth_token_custom_registry_challenge(monkeypatch):
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        if req.full_url == "https://reg.example/v2/":
            hdrs = email.message.Message()
            hdrs["WWW-Authenticate"] = (
                'Bearer realm="https://auth.reg/token",service="reg.example"'
            )
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", hdrs, None
            )
        return _FakeResp({"token": "CUSTOM_TKN"})

    _patch_opener(monkeypatch, fake_open)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok, base = transport.get_auth_token(
        "team/app", registry="reg.example", actions="pull,push"
    )
    assert tok == "CUSTOM_TKN"
    assert base == "https://reg.example"
    # Probed /v2/ over HTTPS then redeemed the realm with scope+service.
    assert calls[0] == "https://reg.example/v2/"
    assert any("scope=repository:team/app:pull,push" in u for u in calls[1:])
    assert any("service=reg.example" in u for u in calls[1:])


def test_get_auth_token_open_registry_returns_empty(monkeypatch):
    _patch_opener(monkeypatch, lambda req, *a, **k: _FakeResp({}))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok, base = transport.get_auth_token("x/y", registry="open.example")
    assert tok == ""
    assert base == "https://open.example"


# ----- insecure transport: HTTP-only registries ---------------------------

def test_registry_base_url_scheme():
    assert transport.registry_base_url("reg.example") == "https://reg.example"
    assert (transport.registry_base_url("reg.example", insecure=True)
            == "http://reg.example")
    # Docker Hub (empty registry) is always HTTPS, even when insecure.
    assert (transport.registry_base_url("", insecure=True)
            == transport.REGISTRY_URL)


def test_insecure_registry_msg_mentions_flag():
    m = transport.insecure_registry_msg("192.168.1.1:5000")
    assert "192.168.1.1:5000" in m
    assert "--allow-insecure" in m


def test_get_auth_token_insecure_bad_cert_uses_https(monkeypatch):
    # Under --allow-insecure, HTTPS is tried first (so a registry with an
    # untrusted certificate is reached); no HTTP fallback when HTTPS answers.
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        return _FakeResp({})

    _patch_opener(monkeypatch, fake_open)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok, base = transport.get_auth_token(
        "x/y", registry="reg.example", insecure=True
    )
    assert tok == ""
    assert base == "https://reg.example"
    assert calls == ["https://reg.example/v2/"]


def test_get_auth_token_insecure_http_only_falls_back_to_http(monkeypatch):
    # Under --allow-insecure, an HTTP-only registry (its HTTPS probe returns
    # plaintext) is retried over http:// and resolves to an http base.
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        if req.full_url.startswith("https://"):
            raise urllib.error.URLError(_ssl_error("WRONG_VERSION_NUMBER"))
        return _FakeResp({})

    _patch_opener(monkeypatch, fake_open)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok, base = transport.get_auth_token(
        "x/y", registry="reg.example", insecure=True
    )
    assert tok == ""
    assert base == "http://reg.example"
    assert calls == ["https://reg.example/v2/", "http://reg.example/v2/"]


def test_get_auth_token_http_only_registry_raises(monkeypatch):
    # Enforcing HTTPS: the HTTPS probe gets a plaintext WRONG_VERSION_NUMBER.
    # The SSL signature alone drives the friendly error, even when the active
    # HTTP re-probe is unavailable.
    def fake_open(req, *a, **k):
        raise urllib.error.URLError(_ssl_error("WRONG_VERSION_NUMBER"))

    _patch_opener(monkeypatch, fake_open)
    monkeypatch.setattr(transport, "_http_registry_reachable",
                        lambda *a, **k: False)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError) as exc:
        transport.get_auth_token("x/y", registry="reg.example")
    assert "--allow-insecure" in str(exc.value)
    assert "reg.example" in str(exc.value)


def test_get_auth_token_http_only_via_active_probe(monkeypatch):
    # Enforcing HTTPS: HTTPS fails with a non-conclusive error, but an active
    # HTTP /v2/ re-probe confirms the registry is HTTP-only.
    def fake_open(req, *a, **k):
        raise urllib.error.URLError("connection reset")

    _patch_opener(monkeypatch, fake_open)
    monkeypatch.setattr(transport, "_http_registry_reachable",
                        lambda *a, **k: True)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError) as exc:
        transport.get_auth_token("x/y", registry="reg.example")
    assert "--allow-insecure" in str(exc.value)


def test_get_auth_token_unreachable_reraises_urlerror(monkeypatch):
    # HTTPS fails and the registry is not reachable over HTTP either -> a
    # genuine outage; the original error propagates (no insecure hint).
    def fake_open(req, *a, **k):
        raise urllib.error.URLError("offline")

    _patch_opener(monkeypatch, fake_open)
    monkeypatch.setattr(transport, "_http_registry_reachable",
                        lambda *a, **k: False)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(urllib.error.URLError):
        transport.get_auth_token("x/y", registry="reg.example")


# ----- insecure transport: bad TLS certificates ---------------------------

def test_get_auth_token_cert_error_raises_meaningful(monkeypatch):
    # Enforcing HTTPS: an untrusted certificate yields a meaningful error
    # pointing at --allow-insecure (not a raw SSL error, not "HTTP-only").
    def fake_open(req, *a, **k):
        raise urllib.error.URLError(_ssl_error("CERTIFICATE_VERIFY_FAILED"))

    _patch_opener(monkeypatch, fake_open)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError) as exc:
        transport.get_auth_token("x/y", registry="reg.example")
    assert "--allow-insecure" in str(exc.value)
    assert "certificate" in str(exc.value).lower()


# ----- message helpers ----------------------------------------------------

def test_auth_note(monkeypatch):
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    assert transport.auth_note() == " (anonymous)"
    monkeypatch.setenv("PD_DOCKER_AUTH", "u:p")
    assert transport.auth_note() == " (user credentials)"


def test_denied_messages_mention_auth_env(monkeypatch):
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    assert "PD_DOCKER_AUTH" in transport.auth_denied_msg("ubuntu", 401)
    assert "PD_DOCKER_AUTH" in transport.push_denied_msg("me/app", 403)
    monkeypatch.setenv("PD_DOCKER_AUTH", "u:p")
    assert "correct" in transport.auth_denied_msg("ubuntu", 403)
