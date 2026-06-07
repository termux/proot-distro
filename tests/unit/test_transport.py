# Tests for proot_distro.helpers.docker.transport — auth challenge parsing,
# PD_DOCKER_AUTH handling, cross-host Authorization stripping, and the token
# exchange (with urlopen mocked; no network).

import base64
import email.message
import io
import json

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


# ----- token exchange (mocked urlopen) ------------------------------------

class _FakeResp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def read(self, *a):
        return self._buf.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_get_auth_token_docker_hub(monkeypatch):
    captured = {}

    def fake_urlopen(req, *a, **k):
        captured["url"] = req.full_url
        return _FakeResp({"token": "DOCKERHUB_TKN"})

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok = transport.get_auth_token("library/ubuntu")
    assert tok == "DOCKERHUB_TKN"
    assert "scope=repository:library/ubuntu:pull" in captured["url"]


def test_get_auth_token_custom_registry_challenge(monkeypatch):
    calls = []

    def fake_urlopen(req, *a, **k):
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

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok = transport.get_auth_token("team/app", registry="reg.example",
                                   actions="pull,push")
    assert tok == "CUSTOM_TKN"
    # Probed /v2/ then redeemed the realm with the right scope+service.
    assert calls[0] == "https://reg.example/v2/"
    assert any("scope=repository:team/app:pull,push" in u for u in calls[1:])
    assert any("service=reg.example" in u for u in calls[1:])


def test_get_auth_token_open_registry_returns_empty(monkeypatch):
    def fake_urlopen(req, *a, **k):
        return _FakeResp({})  # /v2/ answered 200 -> no auth needed

    monkeypatch.setattr(transport.urllib.request, "urlopen", fake_urlopen)
    assert transport.get_auth_token("x/y", registry="open.example") == ""


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
