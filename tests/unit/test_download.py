# Tests for proot_distro.helpers.download — the shared plaintext-HTTP TLS
# error classifier, download_file's handling of an https:// URL that is
# actually served over plain HTTP (the opener mocked; no network), and the
# redirect policy every opener in the program is built with.

import email.message
import http.client
import io
import ssl

import urllib.error
import urllib.request
import urllib.response

import pytest

from proot_distro.helpers import download


def _ssl_error(reason: str) -> ssl.SSLError:
    err = ssl.SSLError(1, f"[SSL: {reason}] {reason.lower()}")
    err.reason = reason
    return err


class _FakeDownloadResp:
    """Minimal urlopen response: yields *data* once, then EOF."""

    def __init__(self, data: bytes):
        self._data = data
        self._sent = False
        self.headers = {"Content-Length": str(len(data))}

    def read(self, n: int = -1) -> bytes:
        if self._sent:
            return b""
        self._sent = True
        return self._data

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    """Stand-in for the opener download.http_opener() returns."""

    def __init__(self, fn):
        self._fn = fn

    def open(self, req, *a, **k):
        return self._fn(req, *a, **k)


def _patch_open(monkeypatch, fn, seen=None):
    """Route download_file's requests to *fn*; record the opener's flags."""
    def fake_http_opener(**kwargs):
        if seen is not None:
            seen.update(kwargs)
        return _FakeOpener(fn)

    monkeypatch.setattr(download, "http_opener", fake_http_opener)


# ----- error classifiers --------------------------------------------------

def test_is_plaintext_http_tls_error():
    # A WRONG_VERSION_NUMBER handshake error == peer replied with plaintext.
    plain = urllib.error.URLError(_ssl_error("WRONG_VERSION_NUMBER"))
    assert download.is_plaintext_http_tls_error(plain)
    # A non-SSL connection failure is not a plaintext signal.
    assert not download.is_plaintext_http_tls_error(
        urllib.error.URLError("connection refused")
    )
    # A genuine TLS failure (untrusted/expired cert) is not plaintext —
    # must not be misread as an HTTP-only server.
    cert = urllib.error.URLError(_ssl_error("CERTIFICATE_VERIFY_FAILED"))
    assert not download.is_plaintext_http_tls_error(cert)


def test_is_cert_verification_error():
    # An untrusted/expired/self-signed certificate.
    cert = urllib.error.URLError(_ssl_error("CERTIFICATE_VERIFY_FAILED"))
    assert download.is_cert_verification_error(cert)
    # The real type raised by urllib is SSLCertVerificationError.
    typed = urllib.error.URLError(
        ssl.SSLCertVerificationError(1, "self-signed certificate")
    )
    assert download.is_cert_verification_error(typed)
    # A plaintext-HTTP reply is a different failure, not a cert error.
    assert not download.is_cert_verification_error(
        urllib.error.URLError(_ssl_error("WRONG_VERSION_NUMBER"))
    )
    # A plain connection failure is not a cert error.
    assert not download.is_cert_verification_error(
        urllib.error.URLError("connection refused")
    )


def test_insecure_ssl_context_skips_verification():
    ctx = download.insecure_ssl_context()
    assert ctx.check_hostname is False
    assert ctx.verify_mode == ssl.CERT_NONE


# ----- shared retry policy ------------------------------------------------

def test_is_retryable_http_error():
    # 5xx and plain network failures are transient -> retry.
    assert download.is_retryable_http_error(
        urllib.error.HTTPError("u", 503, "x", email.message.Message(), None)
    )
    assert download.is_retryable_http_error(urllib.error.URLError("reset"))
    assert download.is_retryable_http_error(OSError("broken pipe"))
    # 408/429 mean "retry later" -> retry.
    assert download.is_retryable_http_error(
        urllib.error.HTTPError("u", 429, "x", email.message.Message(), None)
    )
    # Other 4xx are deterministic -> do not retry.
    for code in (400, 401, 403, 404, 410):
        assert not download.is_retryable_http_error(
            urllib.error.HTTPError("u", code, "x", email.message.Message(), None)
        )
    # TLS cert and plaintext-HTTP failures are deterministic -> do not retry.
    assert not download.is_retryable_http_error(
        urllib.error.URLError(_ssl_error("CERTIFICATE_VERIFY_FAILED"))
    )
    assert not download.is_retryable_http_error(
        urllib.error.URLError(_ssl_error("WRONG_VERSION_NUMBER"))
    )
    # A body that ended early is the wire's fault, not the request's.
    assert download.is_retryable_http_error(http.client.IncompleteRead(b"ab"))
    assert download.is_retryable_http_error(download.IncompleteResponse("x"))
    assert download.is_retryable_http_error(http.client.BadStatusLine("x"))
    # A URL http.client will not put on the wire cannot succeed on a repeat.
    assert not download.is_retryable_http_error(http.client.InvalidURL("port"))


def test_retry_http_retries_then_succeeds(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)
    logged = []
    monkeypatch.setattr(download, "log_info", lambda text: logged.append(text))
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise urllib.error.URLError("reset")
        return "ok"

    assert download.retry_http(op, what="Job", max_retries=5) == "ok"
    assert attempts["n"] == 3
    assert len([t for t in logged if "retrying" in t.lower()]) == 2


def test_retry_http_fail_fast_on_deterministic(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        raise urllib.error.HTTPError(
            "u", 404, "Not Found", email.message.Message(), None
        )

    with pytest.raises(urllib.error.HTTPError):
        download.retry_http(op, what="Job", max_retries=5)
    assert attempts["n"] == 1  # deterministic -> no retries


def test_retry_http_reraises_after_exhaustion(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        raise urllib.error.URLError("reset")

    with pytest.raises(urllib.error.URLError):
        download.retry_http(op, what="Job", max_retries=3)
    assert attempts["n"] == 3


# ----- download_file plaintext detection ----------------------------------

def test_download_file_plaintext_https_meaningful_error(tmp_path, monkeypatch):
    # An https:// URL served by a plaintext-HTTP server must fail fast with a
    # meaningful error (mentioning plain HTTP and the http:// remedy), not a
    # raw SSL error after exhausting all retries.
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        raise urllib.error.URLError(_ssl_error("WRONG_VERSION_NUMBER"))

    _patch_open(monkeypatch, fake_open)
    dest = tmp_path / "rootfs.tar"
    with pytest.raises(RuntimeError) as exc:
        download.download_file(
            "https://example.com/rootfs.tar", str(dest),
            max_retries=5, retry_delay=0,
        )
    message = str(exc.value)
    assert "plain HTTP" in message
    assert "http://" in message
    assert "example.com" in message
    assert len(calls) == 1  # deterministic error -> no pointless retries


# ----- download_file certificate handling ---------------------------------

def test_download_file_cert_error_meaningful(tmp_path, monkeypatch):
    # An https:// URL with an untrusted certificate must fail fast with a
    # meaningful error pointing at --allow-insecure, not a raw SSL error after
    # exhausting all retries.
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        raise urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "self-signed certificate")
        )

    _patch_open(monkeypatch, fake_open)
    dest = tmp_path / "rootfs.tar"
    with pytest.raises(RuntimeError) as exc:
        download.download_file(
            "https://example.com/rootfs.tar", str(dest),
            max_retries=5, retry_delay=0,
        )
    message = str(exc.value)
    assert "certificate" in message.lower()
    assert "--allow-insecure" in message
    assert "example.com" in message
    assert len(calls) == 1  # deterministic error -> no pointless retries


# ----- download_file fail-fast on HTTP errors -----------------------------

def test_download_file_404_fails_fast(tmp_path, monkeypatch):
    # A 404 is deterministic: the URL is wrong and retrying cannot fix it, so
    # the download must fail immediately (one request, no retry delays) with a
    # meaningful HTTP error.
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", email.message.Message(), None
        )

    _patch_open(monkeypatch, fake_open)
    dest = tmp_path / "rootfs.tar"
    with pytest.raises(RuntimeError) as exc:
        download.download_file(
            "https://example.com/rootfs.tar", str(dest),
            max_retries=5, retry_delay=0,
        )
    message = str(exc.value)
    assert "404" in message
    assert "Not Found" in message
    assert len(calls) == 1  # deterministic error -> no pointless retries


def test_download_file_server_error_retries(tmp_path, monkeypatch):
    # A 5xx is potentially transient, so it must still be retried up to the
    # limit before failing (i.e. fail-fast applies to client errors only).
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        raise urllib.error.HTTPError(
            req.full_url, 503, "Service Unavailable",
            email.message.Message(), None
        )

    _patch_open(monkeypatch, fake_open)
    dest = tmp_path / "rootfs.tar"
    with pytest.raises(RuntimeError):
        download.download_file(
            "https://example.com/rootfs.tar", str(dest),
            max_retries=3, retry_delay=0,
        )
    assert len(calls) == 3  # exhausted every retry


def test_download_file_retry_is_logged(tmp_path, monkeypatch):
    # Each transient failure that is about to be retried must be logged so the
    # user sees the retry happening (attempt number and the underlying error),
    # and no retry line is logged for the final, fatal attempt.
    logged = []
    monkeypatch.setattr(download, "log_info", lambda text: logged.append(text))

    def fake_open(req, *a, **k):
        raise urllib.error.URLError("connection reset")

    _patch_open(monkeypatch, fake_open)
    dest = tmp_path / "rootfs.tar"
    with pytest.raises(RuntimeError):
        download.download_file(
            "https://example.com/rootfs.tar", str(dest),
            max_retries=3, retry_delay=0,
        )
    retry_lines = [t for t in logged if "retrying" in t.lower()]
    assert len(retry_lines) == 2  # logged before retry 2 and 3, not the last
    assert "attempt 1/3" in retry_lines[0]
    assert "connection reset" in retry_lines[0]


def test_download_file_no_retry_log_on_fail_fast(tmp_path, monkeypatch):
    # A deterministic 404 fails immediately, so there must be no retry log.
    logged = []
    monkeypatch.setattr(download, "log_info", lambda text: logged.append(text))

    def fake_open(req, *a, **k):
        raise urllib.error.HTTPError(
            req.full_url, 404, "Not Found", email.message.Message(), None
        )

    _patch_open(monkeypatch, fake_open)
    dest = tmp_path / "rootfs.tar"
    with pytest.raises(RuntimeError):
        download.download_file(
            "https://example.com/rootfs.tar", str(dest),
            max_retries=5, retry_delay=0,
        )
    assert not [t for t in logged if "retrying" in t.lower()]


def test_download_file_insecure_uses_the_insecure_opener(tmp_path, monkeypatch):
    # With insecure=True the download goes through the opener built for
    # --allow-insecure, so a bad certificate no longer blocks it.
    seen = {}
    _patch_open(monkeypatch, lambda req, *a, **k: _FakeDownloadResp(b"payload"),
                seen)
    dest = tmp_path / "rootfs.tar"
    download.download_file(
        "https://example.com/rootfs.tar", str(dest), insecure=True,
    )
    assert seen == {"insecure": True}
    assert dest.read_bytes() == b"payload"


def test_download_file_verifies_by_default(tmp_path, monkeypatch):
    seen = {}
    _patch_open(monkeypatch, lambda req, *a, **k: _FakeDownloadResp(b"x"),
                seen)
    download.download_file("https://example.com/r.tar", str(tmp_path / "o"))
    assert seen == {"insecure": False}


def _https_handler(opener):
    return next(h for h in opener.handlers
                if isinstance(h, urllib.request.HTTPSHandler))


def test_insecure_opener_skips_certificate_verification():
    ctx = _https_handler(download.http_opener(insecure=True))._context
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_NONE


def test_verifying_opener_uses_the_default_context():
    # What urlopen() itself gets: older interpreters keep None and let
    # http.client build the default context per connection, newer ones
    # build it in the handler. Either way it verifies.
    ctx = _https_handler(download.http_opener())._context
    assert ctx is None or (
        ctx.verify_mode == ssl.CERT_REQUIRED and ctx.check_hostname
    )


# ----- a response that ends early -----------------------------------------
#
# Two framings, two failures. A **chunked** body whose peer disappears
# raises http.client.IncompleteRead, which is not an OSError and so walked
# through every `except (URLError, OSError)` in the program. A
# **Content-Length** body cut short raises nothing at all — CPython's
# HTTPResponse.read(amt) deliberately does not — so the short bytes were
# simply what the caller got, and `install <url>` published them.


def test_network_errors_covers_the_http_client_family():
    assert http.client.HTTPException in download.NETWORK_ERRORS
    assert issubclass(download.IncompleteResponse, http.client.HTTPException)
    # IncompleteRead is the one a truncated chunked body really raises.
    assert isinstance(
        http.client.IncompleteRead(b"ab"), download.NETWORK_ERRORS
    )
    # ... and it is not an OSError, which is why the old nets missed it.
    assert not isinstance(http.client.IncompleteRead(b"ab"), OSError)


class _Headers(dict):
    """Enough of email.message.Message for declared_length()."""


@pytest.mark.parametrize("value,expected", [
    ({"Content-Length": "12"}, 12),
    ({"Content-Length": "0"}, 0),
    ({}, 0),
    ({"Content-Length": "abc"}, 0),      # a header is the server's string
    ({"Content-Length": "-5"}, 0),
    ({"Content-Length": None}, 0),
])
def test_declared_length_never_raises_on_a_header(value, expected):
    resp = type("R", (), {"headers": _Headers(value)})()
    assert download.declared_length(resp) == expected


def test_require_complete_body():
    download.require_complete_body(10, 10, "x")     # exact
    download.require_complete_body(10, 0, "x")      # nothing declared
    download.require_complete_body(0, 0, "x")
    with pytest.raises(download.IncompleteResponse) as exc:
        download.require_complete_body(3, 10, "Downloading u")
    assert "3 of 10" in str(exc.value)
    assert "Downloading u" in str(exc.value)
    # No label for a caller that already names the URL in its own message.
    with pytest.raises(download.IncompleteResponse) as exc:
        download.require_complete_body(3, 10)
    assert str(exc.value).startswith("the response ended")


def test_retry_http_retries_a_body_that_ended_early(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(download, "log_info", lambda text: None)
    attempts = {"n": 0}

    def op():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise http.client.IncompleteRead(b"partial")
        return "ok"

    assert download.retry_http(op, what="Job", max_retries=5) == "ok"
    assert attempts["n"] == 3


def test_download_file_body_short_of_its_length_is_a_message(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(download, "log_info", lambda text: None)

    class _Short(_FakeDownloadResp):
        def __init__(self):
            super().__init__(b"half")
            self.headers = {"Content-Length": "1000"}

    _patch_open(monkeypatch, lambda *a, **k: _Short())
    dest = tmp_path / "out.tar"
    with pytest.raises(RuntimeError) as exc:
        download.download_file("http://h/x.tar", str(dest), max_retries=2)
    assert "4 of 1000" in str(exc.value)
    # The temporary went with the failure: nothing publishes half an archive.
    assert not dest.exists()


def test_download_file_incomplete_read_is_a_message(tmp_path, monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(download, "log_info", lambda text: None)

    class _Cut(_FakeDownloadResp):
        def __init__(self):
            super().__init__(b"")
            self.headers = {}

        def read(self, n=-1):
            raise http.client.IncompleteRead(b"partial", 900)

    _patch_open(monkeypatch, lambda *a, **k: _Cut())
    dest = tmp_path / "out.tar"
    with pytest.raises(RuntimeError):
        download.download_file("http://h/x.tar", str(dest), max_retries=2)
    assert not dest.exists()


# ----- the redirect policy ------------------------------------------------
#
# Driven through a real opener from http_opener(): a canned handler sits in
# front of the HTTP(S) handlers and answers from a route table, so the
# redirect travels urllib's whole error-processing chain -- the path a live
# 302 takes -- without a socket being opened.

class _Canned(urllib.request.BaseHandler):
    handler_order = 100  # ahead of HTTPHandler/HTTPSHandler

    def __init__(self, routes):
        self.routes = routes
        self.seen = []
        self.bodies = []

    def _answer(self, req):
        self.seen.append(req.full_url)
        code, location, body = self.routes[req.full_url]
        headers = email.message.Message()
        if location is not None:
            headers["Location"] = location
        headers["Content-Length"] = str(len(body))
        fp = io.BytesIO(body)
        self.bodies.append(fp)
        resp = urllib.response.addinfourl(fp, headers, req.full_url, code)
        resp.msg = "Found" if 300 <= code < 400 else "OK"
        return resp

    http_open = https_open = _answer


def _open_through(routes, url, **opener_kw):
    canned = _Canned(routes)
    opener = download.http_opener(**opener_kw)
    opener.add_handler(canned)
    try:
        with opener.open(url) as resp:
            return canned, resp.read()
    except BaseException as exc:
        exc.canned = canned
        raise


def test_https_to_http_redirect_is_refused():
    routes = {
        "https://a.example/x.tar": (302, "http://b.example/x.tar", b""),
        "http://b.example/x.tar": (200, None, b"tampered"),
    }
    with pytest.raises(download.RedirectRefused) as exc:
        _open_through(routes, "https://a.example/x.tar")
    assert exc.value.plaintext
    assert "plain HTTP" in str(exc.value)
    # Refused before the plaintext hop was ever requested, and the 3xx
    # response's connection was released rather than left open.
    assert exc.value.canned.seen == ["https://a.example/x.tar"]
    assert exc.value.canned.bodies[0].closed


def test_https_to_http_redirect_is_followed_under_insecure():
    routes = {
        "https://a.example/x.tar": (302, "http://b.example/x.tar", b""),
        "http://b.example/x.tar": (200, None, b"payload"),
    }
    canned, body = _open_through(routes, "https://a.example/x.tar",
                                 insecure=True)
    assert body == b"payload"
    assert canned.seen[-1] == "http://b.example/x.tar"


@pytest.mark.parametrize("start,location,final", [
    # An upgrade, and what the user already chose, are both followed.
    ("http://a.example/x", "https://b.example/x", "https://b.example/x"),
    ("http://a.example/x", "http://b.example/x", "http://b.example/x"),
    ("https://a.example/x", "https://cdn.example/x", "https://cdn.example/x"),
    # Relative and scheme-relative targets keep the scheme they came from.
    ("https://a.example/v2/x", "/blobs/y", "https://a.example/blobs/y"),
    ("https://a.example/x", "//cdn.example/y", "https://cdn.example/y"),
])
def test_redirects_that_keep_or_gain_tls_are_followed(start, location, final):
    routes = {start: (302, location, b""), final: (200, None, b"ok")}
    canned, body = _open_through(routes, start)
    assert body == b"ok"
    assert canned.seen == [start, final]


@pytest.mark.parametrize("location", [
    "ftp://b.example/x.tar",       # urllib's own handler would follow it
    "file:///etc/passwd",
    "data:,hello",
    "gopher://b.example/x",
    "https:///no-host",            # a scheme change naming no host
])
@pytest.mark.parametrize("insecure", [False, True])
def test_redirects_off_http_are_refused(location, insecure):
    # Never retried: the stdlib's own refusal was an HTTPError carrying
    # the 3xx, which retry_http reads as transient and repeated.
    routes = {"http://a.example/x.tar": (302, location, b"")}
    with pytest.raises(download.RedirectRefused) as exc:
        _open_through(routes, "http://a.example/x.tar", insecure=insecure)
    assert not exc.value.plaintext
    assert not download.is_retryable_http_error(exc.value)


@pytest.mark.parametrize("location", ["http://[/x", "https://[::1/x"])
def test_a_malformed_location_is_a_refusal_not_a_value_error(location):
    # urllib parses the header on the way to following it, and its
    # ValueError came out of the middle of the request as a traceback.
    routes = {"https://a.example/x": (301, location, b"")}
    with pytest.raises(download.RedirectRefused) as exc:
        _open_through(routes, "https://a.example/x")
    assert "not a valid URL" in str(exc.value)


def test_a_redirect_target_is_quoted_in_the_message():
    # The Location header is the server's text and goes to the terminal.
    routes = {"https://a.example/x": (302, "http://b.example/\x1b[2J", b"")}
    with pytest.raises(download.RedirectRefused) as exc:
        _open_through(routes, "https://a.example/x")
    assert "\x1b" not in str(exc.value)


def test_the_opener_speaks_only_http_and_https():
    for url in ("file:///etc/passwd", "ftp://a.example/x", "data:,hi"):
        with pytest.raises(urllib.error.URLError, match="unknown url type"):
            download.http_opener().open(url)


def test_download_file_refused_downgrade_names_the_remedy(tmp_path,
                                                          monkeypatch):
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        raise download.RedirectRefused("Refusing to follow ...",
                                       plaintext=True)

    _patch_open(monkeypatch, fake_open)
    dest = tmp_path / "rootfs.tar"
    with pytest.raises(RuntimeError) as exc:
        download.download_file("https://example.com/rootfs.tar", str(dest),
                               max_retries=5, retry_delay=0)
    assert "--allow-insecure" in str(exc.value)
    assert len(calls) == 1
    assert not dest.exists()


def test_download_file_refused_scheme_offers_no_flag(tmp_path, monkeypatch):
    # --allow-insecure does not permit ftp://, so it is not suggested.
    def fake_open(req, *a, **k):
        raise download.RedirectRefused("Refusing to follow ...")

    _patch_open(monkeypatch, fake_open)
    with pytest.raises(RuntimeError) as exc:
        download.download_file("https://example.com/rootfs.tar",
                               str(tmp_path / "o"), retry_delay=0)
    assert "--allow-insecure" not in str(exc.value)


# ----- a request urllib cannot build or send ------------------------------
#
# urllib answers much of what is wrong with a URL by raising ValueError (or
# UnicodeError) out of the middle of the request. None of it is an OSError
# or an HTTPException, so every net let it through as a traceback.

@pytest.fixture
def no_proxy(monkeypatch):
    # The opener reads proxies from the environment; a request routed to a
    # proxy would fail somewhere else, or not at all.
    for var in ("http_proxy", "https_proxy", "all_proxy",
                "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(var, raising=False)


@pytest.mark.parametrize("url", [
    "http://[", "https://[:5000/v2/", "https://[::1/x", "http://[x]/",
    "http://exa℀mple/",           # NFKC folds a '/' into the host
])
def test_new_request_refuses_a_malformed_url(url):
    with pytest.raises(download.InvalidRequest) as exc:
        download.new_request(url, headers={"User-Agent": "pd"})
    assert "not a valid URL" in str(exc.value)
    assert not download.is_retryable_http_error(exc.value)
    assert isinstance(exc.value, download.NETWORK_ERRORS)


def test_new_request_builds_an_ordinary_request():
    req = download.new_request("https://a.example/x", method="HEAD",
                                headers={"User-Agent": "pd"})
    assert req.full_url == "https://a.example/x"
    assert req.get_method() == "HEAD"


def test_the_opener_refuses_a_malformed_url_given_as_a_string():
    # A string is parsed inside open(); ADD hands its URL over that way.
    with pytest.raises(download.InvalidRequest):
        download.http_opener().open("http://[")


@pytest.mark.parametrize("url", [
    "http://..example/",               # IDNA: empty label
    "http://" + "a" * 70 + ".example/",  # IDNA: label too long
    "http://127.0.0.1:9/tä.tar",  # a path http.client cannot encode
])
def test_the_opener_refuses_what_it_cannot_send(no_proxy, url):
    # Each fails before a byte leaves: the host is encoded on the way to
    # the resolver, the request line before the connection is made.
    with pytest.raises(download.InvalidRequest) as exc:
        download.http_opener().open(url)
    assert "cannot send a request for" in str(exc.value)
    assert not download.is_retryable_http_error(exc.value)


def test_a_value_error_from_a_redirect_hop_names_the_hop():
    class _Hop(_Canned):
        def _answer(self, req):
            if req.full_url == "https://b.example/x":
                raise UnicodeError("label empty or too long")
            return super()._answer(req)

        http_open = https_open = _answer

    canned = _Hop({"https://a.example/x": (302, "https://b.example/x", b"")})
    opener = download.http_opener()
    opener.add_handler(canned)
    with pytest.raises(download.InvalidRequest) as exc:
        opener.open("https://a.example/x")
    assert "https://b.example/x" in str(exc.value)


def test_retry_http_does_not_repeat_an_invalid_request(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)
    calls = []

    def op():
        calls.append(1)
        raise download.InvalidRequest("x")

    with pytest.raises(download.InvalidRequest):
        download.retry_http(op, what="Job", max_retries=5)
    assert len(calls) == 1


@pytest.mark.parametrize("url", ["http://[", "https://[::1/x.tar"])
def test_download_file_malformed_url_is_a_message(tmp_path, url):
    dest = tmp_path / "rootfs.tar"
    with pytest.raises(RuntimeError) as exc:
        download.download_file(url, str(dest), retry_delay=0)
    assert "not a valid URL" in str(exc.value)
    assert not dest.exists()


# ----- no request waits forever --------------------------------------------

def test_the_opener_gives_every_request_the_network_timeout():
    canned = _Canned({"https://a.example/x": (200, None, b"ok")})
    opener = download.http_opener()
    opener.add_handler(canned)

    seen = []
    orig = canned._answer

    def record(req):
        seen.append(req.timeout)
        return orig(req)

    canned.http_open = canned.https_open = record
    with opener.open("https://a.example/x") as resp:
        resp.read()
    # urllib's own default is the socket module's: none at all.
    assert seen == [download.NETWORK_TIMEOUT]
    assert download.NETWORK_TIMEOUT > 0


def test_a_timeout_the_caller_names_is_kept():
    canned = _Canned({"https://a.example/x": (200, None, b"ok")})
    opener = download.http_opener()
    opener.add_handler(canned)
    seen = []
    orig = canned._answer
    canned.https_open = lambda req: (seen.append(req.timeout), orig(req))[1]
    with opener.open("https://a.example/x", timeout=7.5) as resp:
        resp.read()
    assert seen == [7.5]


def test_a_redirect_hop_keeps_the_timeout():
    routes = {
        "https://a.example/x": (302, "https://cdn.example/x", b""),
        "https://cdn.example/x": (200, None, b"ok"),
    }
    canned = _Canned(routes)
    opener = download.http_opener()
    opener.add_handler(canned)
    seen = []
    orig = canned._answer
    canned.https_open = lambda req: (seen.append(req.timeout), orig(req))[1]
    with opener.open("https://a.example/x") as resp:
        resp.read()
    assert seen == [download.NETWORK_TIMEOUT, download.NETWORK_TIMEOUT]


@pytest.fixture
def silent_server():
    """A loopback listener that accepts the connection and never answers.

    The kernel completes the handshake from the backlog, so the request
    goes out and the client then waits on a peer that says nothing: the
    shape that used to hold a command for good.
    """
    import socket
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.bind(("127.0.0.1", 0))
    srv.listen(8)
    try:
        yield f"http://127.0.0.1:{srv.getsockname()[1]}"
    finally:
        srv.close()


def test_a_peer_that_never_answers_times_out(no_proxy, silent_server,
                                             monkeypatch):
    import time as _time
    monkeypatch.setattr(download, "NETWORK_TIMEOUT", 0.3)
    started = _time.monotonic()
    with pytest.raises(download.NETWORK_ERRORS) as exc:
        download.http_opener().open(silent_server + "/x.tar")
    assert _time.monotonic() - started < 10
    # A timeout is transient: worth the retries the policy gives it.
    assert download.is_retryable_http_error(exc.value)


def test_download_file_from_a_silent_peer_ends(no_proxy, silent_server,
                                               tmp_path, monkeypatch):
    monkeypatch.setattr(download, "NETWORK_TIMEOUT", 0.3)
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)
    monkeypatch.setattr(download, "log_info", lambda text: None)
    dest = tmp_path / "rootfs.tar"
    with pytest.raises(RuntimeError, match="timed out"):
        download.download_file(silent_server + "/x.tar", str(dest),
                               max_retries=2, retry_delay=0)
    assert not dest.exists()
