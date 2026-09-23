# Tests for proot_distro.helpers.docker.transport — auth challenge parsing,
# PD_DOCKER_AUTH handling, cross-host Authorization stripping, and the token
# exchange (with urlopen mocked; no network).

import base64
import email.message
import http.client
import io
import json
import ssl

import urllib.error
import urllib.request

import pytest

from proot_distro.helpers import download
from proot_distro.helpers.docker import transport


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    # Registry requests now share download.retry_http; neutralise its sleep so
    # tests exercising transient (retryable) failures don't actually wait.
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)


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


def test_env_basic_auth_carries_the_variables_own_bytes(monkeypatch):
    # os.environ decodes a byte that is not UTF-8 to a lone surrogate, and
    # strict UTF-8 raised on it out of the pull. The credential is the
    # bytes the user set.
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:p\udcffss")
    expected = "Basic " + base64.b64encode(b"alice:p\xffss").decode()
    assert transport.env_basic_auth() == expected


def test_env_basic_auth_requires_colon(monkeypatch):
    monkeypatch.setenv("PD_DOCKER_AUTH", "bare-token-no-colon")
    with pytest.raises(RuntimeError):
        transport.env_basic_auth()


# ----- cross-host Authorization stripping ---------------------------------

def _make_redirect(orig_url, new_url, allow_plaintext=False):
    handler = transport.AuthStrippingRedirectHandler(
        allow_plaintext=allow_plaintext,
    )
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


def test_redirect_strips_auth_on_a_scheme_downgrade():
    # Same host, but https -> http: the token would go over the wire in
    # clear. The netloc comparison this used to be could not see it.
    # Only --allow-insecure follows such a redirect at all, and even then
    # the header does not go with it.
    new_req = _make_redirect(
        "https://reg.example/v2/foo/blobs/sha256:aa",
        "http://reg.example/v2/foo/blobs/sha256:aa",
        allow_plaintext=True,
    )
    assert new_req.get_header("Authorization") is None


def test_redirect_off_https_is_refused_without_insecure():
    # The same redirect on the verifying opener is not followed: the
    # registry answered a TLS request with a way around TLS, and a tag's
    # manifest has no digest to catch what the plaintext hop serves.
    with pytest.raises(download.RedirectRefused) as exc:
        _make_redirect(
            "https://reg.example/v2/foo/manifests/latest",
            "http://reg.example/v2/foo/manifests/latest",
        )
    assert exc.value.plaintext


def test_redirect_to_ftp_is_refused_even_under_insecure():
    with pytest.raises(download.RedirectRefused) as exc:
        _make_redirect(
            "http://reg.example/v2/foo/blobs/sha256:aa",
            "ftp://cdn.example/blob",
            allow_plaintext=True,
        )
    assert not exc.value.plaintext


@pytest.mark.parametrize("insecure", [False, True])
def test_registry_openers_carry_the_policy(insecure):
    # Both openers are built by download.http_opener, around the handler
    # that also strips credentials, with --allow-insecure deciding
    # whether HTTPS may step down to plain HTTP.
    op = transport._build_opener(insecure)
    handlers = [h for h in op.handlers
                if isinstance(h, urllib.request.HTTPRedirectHandler)]
    assert len(handlers) == 1
    assert isinstance(handlers[0], transport.AuthStrippingRedirectHandler)
    assert handlers[0].allow_plaintext is insecure


def test_redirect_strips_auth_on_a_port_change():
    new_req = _make_redirect(
        "https://reg.example/v2/foo/blobs/sha256:aa",
        "https://reg.example:8443/v2/foo/blobs/sha256:aa",
    )
    assert new_req.get_header("Authorization") is None


def test_redirect_keeps_auth_on_an_https_upgrade():
    new_req = _make_redirect(
        "http://reg.example/v2/foo/blobs/sha256:aa",
        "https://reg.example/v2/foo/blobs/sha256:aa",
    )
    assert new_req.get_header("Authorization") == "Bearer TOKEN"


@pytest.mark.parametrize("a, b, same", [
    # The default port spelled out is the same origin.
    ("https://reg.example/v2/", "https://reg.example:443/v2/", True),
    ("http://reg.example:80/v2/", "http://reg.example/v2/", True),
    # Case of the host does not matter; the path never does.
    ("https://Reg.Example/v2/", "https://reg.example/other", True),
    # A different host, scheme downgrade, port change, or a URL that
    # cannot be read at all.
    ("https://reg.example/v2/", "https://evil.example/v2/", False),
    ("https://reg.example/v2/", "http://reg.example/v2/", False),
    ("https://reg.example/v2/", "https://reg.example:8443/v2/", False),
    ("https://reg.example:8443/v2/", "https://reg.example/v2/", False),
    # The upgrade exception holds only on the default ports.
    ("http://reg.example:5000/v2/", "https://reg.example:5000/v2/", False),
    ("https://reg.example/v2/", "https://reg.example:abc/v2/", False),
    ("https://reg.example/v2/", "/v2/relative", False),
    ("https://reg.example/v2/", "file:///etc/passwd", False),
])
def test_same_auth_origin(a, b, same):
    assert transport.same_auth_origin(a, b) is same


# ----- token exchange (mocked) --------------------------------------------

class _FakeResp:
    def __init__(self, payload, headers=None):
        body = json.dumps(payload).encode()
        self._buf = io.BytesIO(body)
        # A real response carries them, and _request_body reads the
        # declared length off them to tell a whole answer from a cut one.
        self.headers = {"Content-Length": str(len(body))} \
            if headers is None else headers

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


def _patch_hub(monkeypatch, fn):
    # The Docker Hub token exchange goes through the auth-stripping opener,
    # never urllib.request.urlopen (whose default redirect handler forwards
    # the Basic header to any host a redirect names).
    monkeypatch.setattr(transport, "auth_opener", lambda: _FakeOpener(fn))


def _ssl_error(reason: str) -> ssl.SSLError:
    err = ssl.SSLError(1, f"[SSL: {reason}] {reason.lower()}")
    err.reason = reason
    return err


def test_get_auth_token_docker_hub(monkeypatch):
    captured = {}

    def fake_urlopen(req, *a, **k):
        captured["url"] = req.full_url
        return _FakeResp({"token": "DOCKERHUB_TKN"})

    _patch_hub(monkeypatch, fake_urlopen)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok, base = transport.get_auth_token("library/ubuntu")
    assert tok == "DOCKERHUB_TKN"
    assert base == transport.REGISTRY_URL
    assert "scope=repository:library/ubuntu:pull" in captured["url"]


def test_hub_token_exchange_goes_through_the_stripping_opener(monkeypatch):
    # The one request carrying the user's password. urlopen() was what it
    # went through, whose redirect handler keeps every header across
    # hosts -- so a redirect off auth.docker.io carried the Basic header
    # to whatever host it named. It now rides the same opener every other
    # registry request does, and that opener's handler drops it.
    seen = {}

    def fake_open(req, *a, **k):
        seen["url"] = req.full_url
        seen["auth"] = req.get_header("Authorization")
        return _FakeResp({"token": "T"})

    _patch_hub(monkeypatch, fake_open)
    monkeypatch.setattr(
        transport.urllib.request, "urlopen",
        lambda *a, **k: pytest.fail("urlopen() must not be used"),
    )
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:secret")
    tok, _base = transport.get_auth_token("library/ubuntu")
    assert tok == "T"
    assert seen["url"].startswith(transport.AUTH_URL)
    assert seen["auth"] == transport.env_basic_auth()


def test_hub_redirect_off_the_auth_host_drops_the_basic_header():
    # What the handler does with that request when auth.docker.io answers
    # with a redirect elsewhere.
    handler = transport.AuthStrippingRedirectHandler()
    req = urllib.request.Request(
        transport.AUTH_URL + "?service=registry.docker.io",
        headers={"Authorization": "Basic QQ=="},
    )
    new_req = handler.redirect_request(
        req, None, 302, "Found", email.message.Message(),
        "https://evil.example/token",
    )
    assert new_req.get_header("Authorization") is None


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


def _challenge_opener(realm, sink):
    """A registry whose /v2/ challenge names *realm*; *sink* gets the rest."""

    def fake_open(req, *a, **k):
        if req.full_url.endswith("/v2/"):
            hdrs = email.message.Message()
            hdrs["WWW-Authenticate"] = f'Bearer realm="{realm}",service="s"'
            raise urllib.error.HTTPError(
                req.full_url, 401, "Unauthorized", hdrs, None
            )
        sink.append((req.full_url, req.get_header("Authorization")))
        return _FakeResp({"token": "T"})

    return fake_open


def test_a_plaintext_realm_is_refused_when_enforcing_tls(monkeypatch):
    # The registry itself answered over HTTPS; its challenge sends the
    # token request -- with the user's password as a Basic header -- to
    # an http:// URL. Nothing is sent there.
    sent = []
    _patch_opener(monkeypatch, _challenge_opener("http://auth.example/t", sent))
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:secret")
    with pytest.raises(RuntimeError) as exc:
        transport.get_auth_token("x/y", registry="reg.example")
    assert sent == []
    assert "--allow-insecure" in str(exc.value)
    assert "http://auth.example/t" in str(exc.value)


def test_a_plaintext_realm_is_allowed_under_insecure(monkeypatch):
    # --allow-insecure is the user asking for plaintext; the realm gets
    # the same allowance the registry does.
    sent = []
    _patch_opener(monkeypatch, _challenge_opener("http://auth.example/t", sent))
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:secret")
    tok, _base = transport.get_auth_token(
        "x/y", registry="reg.example", insecure=True
    )
    assert tok == "T"
    assert len(sent) == 1
    assert sent[0][0].startswith("http://auth.example/t?")
    assert sent[0][1] == transport.env_basic_auth()


@pytest.mark.parametrize("realm", [
    "file:///etc/passwd",           # the opener would open it
    "ftp://auth.example/token",
    "data:application/json,{}",
    "//auth.example/token",         # no scheme
    "auth.example/token",           # no scheme, no host
    "https:///token",               # a scheme and no host
    "javascript:alert(1)",
])
def test_a_realm_that_is_not_an_http_url_is_refused(monkeypatch, realm):
    # Even under --allow-insecure: that flag means "plaintext HTTP is
    # acceptable", not "any URL scheme is".
    for insecure in (False, True):
        sent = []
        _patch_opener(monkeypatch, _challenge_opener(realm, sent))
        monkeypatch.setenv("PD_DOCKER_AUTH", "alice:secret")
        with pytest.raises(RuntimeError, match="Refusing to send"):
            transport.get_auth_token(
                "x/y", registry="reg.example", insecure=insecure
            )
        assert sent == []


def test_a_realm_on_another_https_host_is_used(monkeypatch):
    # The host is the registry's to choose -- registry.gitlab.com sends
    # its clients to gitlab.com -- so only the scheme is held to a rule.
    sent = []
    _patch_opener(monkeypatch, _challenge_opener("https://gitlab.com/jwt/auth",
                                                 sent))
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:secret")
    tok, base = transport.get_auth_token("g/p", registry="registry.gitlab.com")
    assert tok == "T"
    assert base == "https://registry.gitlab.com"
    assert sent[0][0].startswith("https://gitlab.com/jwt/auth?")
    assert sent[0][1] == transport.env_basic_auth()


def test_an_anonymous_request_to_a_plaintext_realm_is_refused_too(monkeypatch):
    # No password to leak, but the token that comes back is what every
    # later request is authenticated with, and the user has not said
    # plaintext was acceptable. One rule, not two.
    sent = []
    _patch_opener(monkeypatch, _challenge_opener("http://auth.example/t", sent))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError, match="--allow-insecure"):
        transport.get_auth_token("x/y", registry="reg.example")
    assert sent == []


def test_a_realm_that_does_not_parse_is_a_message(monkeypatch):
    # urlsplit's ValueError came out of the middle of the pull, past every
    # net, as a traceback.
    for insecure in (False, True):
        sent = []
        _patch_opener(monkeypatch, _challenge_opener("https://[x/token", sent))
        monkeypatch.setenv("PD_DOCKER_AUTH", "alice:secret")
        with pytest.raises(RuntimeError, match="not a valid URL"):
            transport.get_auth_token(
                "x/y", registry="reg.example", insecure=insecure
            )
        assert sent == []


def test_a_realm_is_quoted_in_the_message(monkeypatch):
    # The challenge is the registry's text, and the message is printed.
    sent = []
    _patch_opener(monkeypatch,
                  _challenge_opener("http://auth.example/\x1b[2J", sent))
    with pytest.raises(RuntimeError) as exc:
        transport.get_auth_token("x/y", registry="reg.example")
    assert "\x1b" not in str(exc.value)
    assert sent == []


@pytest.mark.parametrize("token", ["abc\ndef", "abc\r\n", "t\x00k",
                                   "tok\u20ac"])
def test_a_token_no_header_can_carry_is_refused_unprinted(monkeypatch,
                                                           token):
    # http.client refuses it with a ValueError from the next request --
    # a traceback, and one whose message quotes the header, token and all.
    _patch_hub(monkeypatch, lambda req, *a, **k: _FakeResp({"token": token}))
    with pytest.raises(RuntimeError) as exc:
        transport.get_auth_token("library/x")
    assert "cannot carry" in str(exc.value)
    assert token not in str(exc.value)


def test_an_ordinary_token_is_accepted(monkeypatch):
    jwt = "eyJhbGciOiJSUzI1NiJ9.eyJzdWIiOiJ4In0.c2ln-_+/=="
    _patch_hub(monkeypatch, lambda req, *a, **k: _FakeResp({"token": jwt}))
    assert transport.get_auth_token("library/x")[0] == jwt


@pytest.mark.parametrize("registry", ["[:5000", "[::1", "[x]"])
def test_a_registry_the_url_cannot_hold_is_an_invalid_request(monkeypatch,
                                                              registry):
    # The host of a `[:5000/img` reference: urllib's ValueError at the
    # request's construction walked out of install, build and push.
    _patch_opener(monkeypatch,
                  lambda req, *a, **k: pytest.fail("nothing is sent"))
    with pytest.raises(download.InvalidRequest):
        transport.get_auth_token("img", registry=registry)


def test_the_plaintext_probe_answers_no_for_a_malformed_registry():
    assert transport._http_registry_reachable("[:5000") is False


# ----- malformed and oversized registry answers ---------------------------

class _RawResp:
    """A response whose body is whatever bytes the test chose."""

    def __init__(self, body: bytes, headers=None):
        self._buf = io.BytesIO(body)
        self.headers = {"Content-Length": str(len(body))} \
            if headers is None else headers

    def read(self, *a):
        return self._buf.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_decode_json_object_rejects_a_non_object():
    with pytest.raises(RuntimeError):
        transport.decode_json_object(b'["a"]', "Testing")
    with pytest.raises(RuntimeError):
        transport.decode_json_object(b'not json at all', "Testing")
    with pytest.raises(RuntimeError):
        transport.decode_json_object(b'\xff\xfe', "Testing")
    assert transport.decode_json_object(b'{"a": 1}', "Testing") == {"a": 1}


def test_token_endpoint_answering_a_list_is_a_runtime_error(monkeypatch):
    _patch_hub(monkeypatch, lambda req, *a, **k: _RawResp(b'["not", "an", "object"]'))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError):
        transport.get_auth_token("library/ubuntu")


def test_token_endpoint_answering_junk_is_a_runtime_error(monkeypatch):
    _patch_hub(monkeypatch, lambda req, *a, **k: _RawResp(b'<html>proxy error</html>'))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError):
        transport.get_auth_token("library/ubuntu")


def test_token_that_is_not_a_string_is_refused(monkeypatch):
    _patch_hub(monkeypatch, lambda req, *a, **k: _RawResp(b'{"token": {"nested": 1}}'))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError):
        transport.get_auth_token("library/ubuntu")


def test_oversized_token_response_is_refused(monkeypatch):
    monkeypatch.setattr(transport, "MAX_METADATA_BYTES", 64)
    body = b'{"token": "' + b"A" * 4096 + b'"}'
    _patch_hub(monkeypatch, lambda req, *a, **k: _RawResp(body))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError):
        transport.get_auth_token("library/ubuntu")


def test_response_at_the_limit_is_read(monkeypatch):
    payload = b'{"token": "' + b"A" * 40 + b'"}'
    monkeypatch.setattr(transport, "MAX_METADATA_BYTES", len(payload))
    _patch_hub(monkeypatch, lambda req, *a, **k: _RawResp(payload))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok, _base = transport.get_auth_token("library/ubuntu")
    assert tok == "A" * 40


def test_oversized_response_is_not_retried(monkeypatch):
    # A refusal is deterministic: retrying would only fetch it again.
    monkeypatch.setattr(transport, "MAX_METADATA_BYTES", 8)
    calls = []

    def fake_urlopen(req, *a, **k):
        calls.append(req.full_url)
        return _RawResp(b'{"token": "' + b"A" * 512 + b'"}')

    _patch_hub(monkeypatch, fake_urlopen)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError):
        transport.get_auth_token("library/ubuntu")
    assert len(calls) == 1


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


def test_get_auth_token_retries_transient_probe(monkeypatch):
    # A transient network failure on the /v2/ probe is retried (same policy as
    # the URL downloader) and then succeeds — registry pulls now retry too.
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise urllib.error.URLError("connection reset")
        return _FakeResp({})

    _patch_opener(monkeypatch, fake_open)
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    tok, base = transport.get_auth_token("x/y", registry="reg.example")
    assert tok == ""
    assert base == "https://reg.example"
    assert len(calls) == 3  # two transient failures, then success


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


def test_a_metadata_body_short_of_its_declared_length_is_refused(monkeypatch):
    # The registry said how many bytes there were and sent fewer. A short
    # token grant used to parse as far as it went (or fail as "not JSON");
    # it is now the transport's business, and retried like any other
    # connection that ended early.
    _patch_hub(monkeypatch, lambda req, *a, **k: _RawResp(
            b'{"token": "TKN"}', headers={"Content-Length": "9999"},
        ))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(download.IncompleteResponse):
        transport.get_auth_token("library/ubuntu")


def test_the_size_refusal_still_wins_over_the_length_check(monkeypatch):
    # A body past the cap is short of its own Content-Length by
    # construction — the read stops one byte over — so the order matters:
    # "larger than" is the answer, not "ended early".
    body = b"x" * (transport.MAX_METADATA_BYTES + 10)
    _patch_hub(monkeypatch, lambda req, *a, **k: _RawResp(
            body, headers={"Content-Length": str(len(body))},
        ))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(RuntimeError, match="larger than"):
        transport.get_auth_token("library/ubuntu")


def test_a_response_that_is_cut_mid_chunk_is_not_a_traceback(monkeypatch):
    # What a truncated *chunked* body really raises. It is not an OSError,
    # so it used to walk through every net between here and the command.
    class _Cut(_RawResp):
        def read(self, *a):
            raise http.client.IncompleteRead(b"partial", 900)

    _patch_hub(monkeypatch, lambda req, *a, **k: _Cut(b""))
    monkeypatch.delenv("PD_DOCKER_AUTH", raising=False)
    with pytest.raises(http.client.IncompleteRead):
        transport.get_auth_token("library/ubuntu")
