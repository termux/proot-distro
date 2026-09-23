#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

# Architecture: Generic HTTP download utilities and a content-hash helper.
# Both use proot_distro.progress for TTY progress output so the bar looks
# identical to the one drawn by the Docker pull, OCI extraction, and
# backup/restore code paths.
#
# Every request this program makes goes through an opener built by
# http_opener(): HTTP and HTTPS and nothing else, and redirects followed
# only under RedirectPolicyHandler's rule -- never off TLS unless the user
# asked for insecure transport, and never to another scheme at all. The
# registry transport builds its openers here too, with the handler that
# also strips credentials across origins. Requests are built by
# new_request(), and a request urllib cannot build or send is an
# InvalidRequest -- one of NETWORK_ERRORS -- never a bare ValueError.
# And no request waits forever: the opener gives every one of them
# NETWORK_TIMEOUT unless its caller names a timeout of its own.

import hashlib
import http.client
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.atomic import atomic_write
from proot_distro.constants import PROGRAM_NAME, PROGRAM_VERSION
from proot_distro.message import msg, log_info, log_error, quote_path
from proot_distro.progress import clear_bar, draw_bytes_bar, fmt_size


__all__ = (
    "IncompleteResponse",
    "InvalidRequest",
    "NETWORK_ERRORS",
    "NETWORK_TIMEOUT",
    "RedirectPolicyHandler",
    "RedirectRefused",
    "certificate_error_msg",
    "check_redirect",
    "declared_length",
    "download_file",
    "http_opener",
    "insecure_ssl_context",
    "is_cert_verification_error",
    "is_plaintext_http_tls_error",
    "is_retryable_http_error",
    "new_request",
    "require_complete_body",
    "retry_http",
    "sha256_file",
)


# What a failed HTTP request raises, in one name. urllib wraps what it
# can into URLError (itself an OSError), and a socket failure is an
# OSError outright -- but http.client raises its own family for a
# response that is malformed or cut short, and HTTPException is **not**
# an OSError. A chunked body whose peer disappears mid-chunk is the
# ordinary way to meet one: HTTPResponse.read() raises IncompleteRead,
# which walked straight through every `except (URLError, OSError)` in
# this program and ended `install`, `build` or `push` in a traceback.
# Every net that guards a request uses this tuple, so there is one place
# to add the next family rather than a dozen to remember.
NETWORK_ERRORS = (urllib.error.URLError, OSError, http.client.HTTPException)


# How long a request may go with nothing moving before it is given up on,
# in seconds. It is a socket timeout, so it bounds each step rather than
# the whole: the connect and TLS handshake, every send, and every read --
# the wait for the response and then each wait for more of the body. A
# download that keeps moving may take as long as it takes.
#
# Without one, urllib blocks for as long as the peer keeps the connection
# open without a word, and nothing is raised for retry_http to act on: an
# endpoint that accepted the connection and never answered held `install`,
# `build` or `push` for good. What it raises instead (TimeoutError, or a
# URLError wrapping one) is an OSError, so every net catches it and
# retry_http tries again.
#
# Two minutes with not one byte either way is a dead peer, not a slow one,
# and it is long enough for a mobile connection to come back from a gap:
# where the old behaviour was to wait forever, a timeout that fired on a
# slow but live link would be the regression. The one wait that
# legitimately runs longer is a registry committing an uploaded blob
# before it answers the PUT, and push gives that request a timeout of its
# own.
NETWORK_TIMEOUT = 120.0

# Stands for "the caller named no timeout" in _Opener.open(), so the
# default is looked up when the request is made rather than frozen into
# the signature.
_DEFAULT_TIMEOUT = object()


class IncompleteResponse(http.client.HTTPException):
    """A response body that ended before the length it declared.

    Raised by require_complete_body(). An HTTPException so it travels
    with the rest of them -- caught by every net, retried by retry_http
    -- because that is exactly what it is: the same failure http.client
    reports for a truncated *chunked* body, at the one framing where it
    reports nothing at all.
    """


def declared_length(resp) -> int:
    """The body length *resp* declares, or 0 when it declares none.

    A header is a string the server chose, and `int()` on it was the
    program's own contribution to the problem: `Content-Length: abc` is
    a ValueError, which no net here catches. http.client itself already
    treats an unparsable length as absent (it falls back to reading
    until the connection closes), so this answers the same way.
    """
    try:
        value = int(resp.headers.get("Content-Length", 0))
    except (TypeError, ValueError):
        return 0
    return value if value > 0 else 0


def require_complete_body(received: int, declared: int,
                          what: str = "") -> None:
    """Raise IncompleteResponse when less arrived than *declared*.

    The other half of a truncated response, and the quiet one. A cut
    *chunked* body raises IncompleteRead on its own; a cut
    **Content-Length** body raises nothing at all -- CPython's
    HTTPResponse.read(amt) deliberately does not, for compatibility --
    so the short bytes were simply what the caller got. For a layer blob
    the digest caught it after the fact; for `install <url>` and
    `ADD <url>` nothing did, and the truncated file was published as the
    real one.

    Callers pass the length they already read for the progress bar, so
    nothing here reads the header twice. A caller that deliberately
    reads only part of a body (a probe) must not call this.

    *what* names the request for a caller whose exception surfaces as it
    is; one that already wraps it in a message naming the URL leaves it
    out rather than saying so twice.
    """
    if declared and received < declared:
        prefix = f"{what}: " if what else ""
        raise IncompleteResponse(
            f"{prefix}the response ended after {received} of {declared} "
            f"bytes."
        )


class InvalidRequest(http.client.HTTPException):
    """A request that cannot be built or put on the wire as it stands.

    urllib and http.client answer much of what is wrong with a URL by
    raising ValueError -- or its UnicodeError subclass -- out of the
    middle of the request, not a URLError: brackets that do not close a
    valid IPv6 address (`http://[`, the registry of a `[:5000/img`
    reference), a host the IDNA codec refuses (an empty label as in
    `..example`, a label past 63 characters), a path that is not ASCII,
    a header value with a line break in it. None of those is an OSError
    or an HTTPException, so each walked through every net in the program
    and ended `install`, `build` or `push` in a traceback -- from a URL
    on the command line, a Dockerfile, a registry's challenge or upload
    Location, or a redirect. new_request() and the opener's open() turn
    all of it into this: an HTTPException, so every net catches it, and
    never retried, since the same request fails the same way again.
    """


def _reason(exc: BaseException) -> str:
    """What urllib or a codec said was wrong, safe to print.

    Their messages already repr() whatever they quote -- a codec names a
    character as '\\xe4', ipaddress an address as 'x' -- and escaping
    that again would only double the backslashes. The exception is the
    NFKC check, which names the host raw; a message holding a character
    that is not printable is escaped whole.
    """
    text = str(exc)
    return text if text.isprintable() else quote_path(text)


def new_request(url: str, **kwargs) -> urllib.request.Request:
    """urllib.request.Request(*url*, ...), a malformed URL an InvalidRequest.

    The constructor already parses the URL's host (for origin_req_host),
    so a malformed one fails here, before any opener sees it. Every
    request in the program is built through this.
    """
    try:
        return urllib.request.Request(url, **kwargs)
    except ValueError as exc:
        raise InvalidRequest(
            f"'{quote_path(url)}' is not a valid URL: "
            f"{_reason(exc)}"
        ) from exc


class RedirectRefused(http.client.HTTPException):
    """A redirect this program will not follow.

    Raised by check_redirect(). An HTTPException so it travels with the
    rest of them -- every net catches it through NETWORK_ERRORS -- and one
    retry_http never repeats, since the server gives the same answer
    every time. *plaintext* is True when the refusal is a step down from
    HTTPS to plain HTTP, the one kind insecure transport permits, so a
    caller that has an `--allow-insecure` to offer can say so.
    """

    def __init__(self, message: str, plaintext: bool = False):
        super().__init__(message)
        self.plaintext = plaintext


# The schemes a redirect may lead to. The same two every URL this program
# accepts from the user is held to (`install <url>`, `ADD <url>`), so a
# server cannot take a request anywhere the user could not have sent it.
_FOLLOWED_SCHEMES = ("http", "https")


def check_redirect(from_url: str, location: str, *,
                   allow_plaintext: bool = False) -> None:
    """Raise RedirectRefused unless a redirect to *location* may be followed.

    *location* is the header as the server sent it, relative or not, and
    is resolved against *from_url* -- the request that was answered with
    it -- the way the redirect itself will be. Three answers are refusals:

      * a target that is not a URL at all: urllib parses it on the way to
        following it, and a ValueError out of the middle of a request is
        a traceback in every caller;
      * any scheme but http or https, or no host: urllib's own handler
        permits ftp:// as well, which is plaintext by another name and a
        protocol nothing here was ever asked to speak;
      * HTTPS to plain HTTP, unless *allow_plaintext*: the user asked for
        a verified connection and the server answered with a way around
        it. Nothing pins a tag's manifest, an `install <url>` archive or
        an `ADD <url>` file to a digest, so whoever sits on the plaintext
        hop decides what is installed or built, and the user was never
        told TLS had stopped. Under --allow-insecure the user accepted
        insecure transport and the redirect is followed.

    Plain HTTP to HTTPS is an upgrade and HTTP to HTTP is what the user
    already chose, so both are followed. The rule is per hop: once a
    chain has reached HTTPS it stays there.
    """
    try:
        target = urllib.parse.urljoin(from_url, location)
        src = urllib.parse.urlsplit(from_url)
        dst = urllib.parse.urlsplit(target)
    except ValueError:
        raise RedirectRefused(
            f"Refusing to follow a redirect from '{quote_path(from_url)}': "
            f"its target '{quote_path(location)}' is not a valid URL."
        ) from None
    if dst.scheme not in _FOLLOWED_SCHEMES or not dst.hostname:
        raise RedirectRefused(
            f"Refusing to follow a redirect from '{quote_path(from_url)}' "
            f"to '{quote_path(target)}': only http:// and https:// URLs "
            f"naming a host are followed."
        )
    if src.scheme == "https" and dst.scheme == "http" and not allow_plaintext:
        raise RedirectRefused(
            f"Refusing to follow a redirect from '{quote_path(from_url)}' "
            f"to '{quote_path(target)}': it leaves HTTPS for plain HTTP, "
            f"so what arrives would no longer be protected by TLS.",
            plaintext=True,
        )


class RedirectPolicyHandler(urllib.request.HTTPRedirectHandler):
    """urllib's redirect handler, following only what check_redirect allows.

    The check runs twice, both times on purpose. http_error_30x runs it on
    the raw Location before urllib's own handling touches it, because
    that handling parses the header first -- a malformed one is a bare
    ValueError -- and refuses a scheme it dislikes with an HTTPError whose
    3xx code retry_http reads as transient. redirect_request runs it on
    the absolute URL the new request is actually built for, which is what
    a subclass (and a unit test) calls directly.

    *allow_plaintext* is the insecure-transport opt-in: an opener built
    for --allow-insecure passes True so HTTPS may redirect to plain HTTP.
    """

    def __init__(self, allow_plaintext: bool = False):
        super().__init__()
        self.allow_plaintext = allow_plaintext

    def _refuse(self, fp, from_url: str, location: str) -> None:
        try:
            check_redirect(from_url, location,
                           allow_plaintext=self.allow_plaintext)
        except RedirectRefused:
            # The 3xx response is not handed on to anyone (urllib's own
            # refusal gives it to the HTTPError it raises), so release its
            # connection here.
            if fp is not None:
                fp.close()
            raise

    def http_error_302(self, req, fp, code, msg, headers):
        # The header urllib itself will read: `location`, else `uri`.
        location = headers.get("location")
        if location is None:
            location = headers.get("uri")
        if location is not None:
            self._refuse(fp, req.full_url, location)
        return super().http_error_302(req, fp, code, msg, headers)

    http_error_301 = http_error_303 = http_error_307 = http_error_302
    http_error_308 = http_error_302

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        self._refuse(fp, req.full_url, newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Opener(urllib.request.OpenerDirector):
    """An OpenerDirector whose open() reports an unsendable request as one.

    What new_request() does for the constructor, done for everything
    after it: a URL handed over as a string is parsed in here, a host is
    IDNA-encoded on the way to the resolver, a path and the headers are
    encoded on the way to the socket, and a redirect's target goes
    through all of it again -- each of them a ValueError when it fails.
    The request that failed is named, which for a redirect is the hop.

    It is also where a request gets its timeout: NETWORK_TIMEOUT, unless
    the caller passes one. urllib's own default is the socket module's,
    which is none at all. A redirect's hop is opened with the timeout of
    the request it answers, so the whole chain has it.
    """

    def open(self, fullurl, data=None, timeout=_DEFAULT_TIMEOUT):
        if timeout is _DEFAULT_TIMEOUT:
            timeout = NETWORK_TIMEOUT
        if isinstance(fullurl, str):
            # What OpenerDirector.open() would build, built the way every
            # other request is, so a URL that does not parse says so.
            fullurl, data = new_request(fullurl, data=data), None
        try:
            return super().open(fullurl, data, timeout)
        except ValueError as exc:
            url = getattr(fullurl, "full_url", fullurl)
            raise InvalidRequest(
                f"cannot send a request for '{quote_path(str(url))}': "
                f"{_reason(exc)}"
            ) from exc


def http_opener(*, insecure: bool = False, redirect=None):
    """Return an opener for HTTP(S) requests under the redirect policy.

    urllib.request.urlopen() and build_opener() also install handlers for
    ftp://, file:// and data:, and a default redirect handler that follows
    HTTPS to plain HTTP (or to FTP) without a word. Nothing this program
    fetches is legitimately anything but HTTP(S), so the opener built here
    has the default handler set minus those three -- another scheme is
    refused as an unknown URL type wherever it came from -- and a
    RedirectPolicyHandler in place of urllib's redirect handler. A
    request it cannot send is an InvalidRequest, not a ValueError, and a
    request whose caller names no timeout gets NETWORK_TIMEOUT.

    With *insecure* the HTTPS handler skips certificate verification and
    the redirect policy lets HTTPS step down to plain HTTP: both are what
    --allow-insecure means. *redirect* substitutes a handler of the
    caller's own (a RedirectPolicyHandler subclass carrying its own
    *allow_plaintext*), which is how the registry transport adds its
    credential stripping.
    """
    if redirect is None:
        redirect = RedirectPolicyHandler(allow_plaintext=insecure)
    context = insecure_ssl_context() if insecure else None
    opener = _Opener()
    for handler in (
        urllib.request.ProxyHandler(),
        urllib.request.UnknownHandler(),
        urllib.request.HTTPHandler(),
        urllib.request.HTTPSHandler(context=context),
        urllib.request.HTTPDefaultErrorHandler(),
        redirect,
        urllib.request.HTTPErrorProcessor(),
    ):
        opener.add_handler(handler)
    return opener


def insecure_ssl_context() -> ssl.SSLContext:
    """Return an SSL context that skips certificate and hostname checks.

    Used only when the caller explicitly opts in via ``--allow-insecure``,
    so an HTTPS endpoint with an untrusted/expired/self-signed certificate
    (or a hostname mismatch) can still be reached. This disables the
    protection TLS provides against impersonation — never the default.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def is_cert_verification_error(exc: urllib.error.URLError) -> bool:
    """Return True if *exc* is a TLS certificate verification failure.

    Covers an untrusted CA, an expired or self-signed certificate, and a
    hostname mismatch — i.e. the server *does* speak TLS, but its
    certificate is not trusted. Distinct from is_plaintext_http_tls_error,
    which means the peer is not speaking TLS at all.
    """
    reason = getattr(exc, "reason", None)
    if isinstance(reason, ssl.SSLCertVerificationError):
        return True
    return (
        isinstance(reason, ssl.SSLError)
        and getattr(reason, "reason", None) == "CERTIFICATE_VERIFY_FAILED"
    )


def certificate_error_msg(target: str) -> str:
    """Return the error shown when *target* presents an untrusted certificate."""
    return (
        f"TLS certificate verification failed for '{target}' — the server's "
        f"certificate is untrusted, expired, self-signed, or issued for a "
        f"different hostname. If you trust this endpoint, re-run with "
        f"'--allow-insecure' to skip certificate verification."
    )


# OpenSSL handshake-failure reasons that mean the peer answered our TLS
# ClientHello with plaintext bytes — the signature of a server that only
# speaks plain HTTP reached over an https:// URL. WRONG_VERSION_NUMBER is what
# modern OpenSSL reports; the others cover older or edge builds. These are
# *not* emitted for genuine TLS problems (expired/untrusted cert,
# protocol-version mismatch), so matching them does not misclassify a real
# HTTPS endpoint.
_PLAINTEXT_HTTP_TLS_REASONS = frozenset({
    "WRONG_VERSION_NUMBER",
    "UNKNOWN_PROTOCOL",
    "HTTP_REQUEST",
})


def is_plaintext_http_tls_error(exc: urllib.error.URLError) -> bool:
    """Return True if *exc* is a TLS handshake failure caused by the peer
    replying with plaintext HTTP rather than a genuine TLS error.

    ``urlopen`` of an https:// URL against a server that only speaks plain
    HTTP raises ``URLError`` whose ``reason`` is an ``ssl.SSLError`` with a
    telltale reason string (e.g. WRONG_VERSION_NUMBER). That alone proves the
    peer is HTTP-only — no second network probe is needed. Shared by the
    Docker registry transport and the generic URL downloader.
    """
    reason = getattr(exc, "reason", None)
    if not isinstance(reason, ssl.SSLError):
        return False
    return (getattr(reason, "reason", None) or "") in _PLAINTEXT_HTTP_TLS_REASONS


def is_retryable_http_error(exc: BaseException) -> bool:
    """Return True if a failed HTTP request is worth retrying.

    Deterministic failures are not retried — they cannot succeed on a repeat
    request: an HTTP client error (4xx, except 408 Request Timeout and 429 Too
    Many Requests, which mean "retry later"), a TLS certificate verification
    failure, a plaintext-HTTP reply to an https:// URL, a URL http.client
    will not put on the wire at all (InvalidURL, InvalidRequest), or a
    redirect the policy refuses.
    Everything else — 5xx server errors, connection resets, timeouts, DNS
    failures, and a response body that ended early — is treated as transient
    and retried.
    """
    if isinstance(exc, urllib.error.HTTPError):
        return not (400 <= exc.code < 500 and exc.code not in (408, 429))
    if isinstance(exc, urllib.error.URLError):
        if is_cert_verification_error(exc) or is_plaintext_http_tls_error(exc):
            return False
    if isinstance(exc, http.client.InvalidURL):
        # A control character in the URL, or a port that is not a number:
        # the request never left, and will not next time either.
        return False
    if isinstance(exc, RedirectRefused):
        # The server's answer, and it will be the same one next time.
        return False
    if isinstance(exc, InvalidRequest):
        # The request never left: nothing about it changes on a repeat.
        return False
    return True


def retry_http(operation, *, what: str, max_retries: int = 5,
               retry_delay: int = 5):
    """Run *operation* (a zero-arg callable performing one HTTP request),
    retrying transient failures with a delay and a logged notice.

    This is the single retry policy shared by the plain URL downloader and the
    Docker/OCI registry transport, so both behave identically. A deterministic
    failure (see is_retryable_http_error) is re-raised immediately — without
    retrying or logging — so the caller can translate it into a meaningful
    message. The original exception is likewise re-raised once every attempt is
    spent. *what* is a short label for the retry log line.
    """
    for attempt in range(max_retries):
        try:
            return operation()
        except KeyboardInterrupt:
            raise
        except NETWORK_ERRORS as exc:
            if not is_retryable_http_error(exc) or attempt >= max_retries - 1:
                raise
            log_info(
                f"{what}: attempt {attempt + 1}/{max_retries} failed "
                f"({exc}); retrying in {retry_delay}s..."
            )
            time.sleep(retry_delay)


def sha256_file(path: str) -> str:
    """Compute and return the SHA-256 hex digest of *path*, with a progress bar."""
    h = hashlib.sha256()
    total = os.path.getsize(path)
    processed = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
            processed += len(chunk)
            draw_bytes_bar(processed, total, noun="processed")
    clear_bar()
    return h.hexdigest()


def download_file(
    url: str, dest: str, max_retries: int = 5, retry_delay: int = 5,
    insecure: bool = False,
) -> None:
    """Download *url* to *dest* with progress output, redirects, and retries.

    HTTPS certificates are verified by default, and a redirect from HTTPS
    to plain HTTP is refused (see check_redirect). When *insecure* is set
    the download proceeds even if the server presents an untrusted/
    expired/self-signed certificate, or redirects to plain HTTP — the
    opt-in behaviour behind the install command's ``--allow-insecure``.
    """
    opener = http_opener(insecure=insecure)
    try:
        host = urllib.parse.urlsplit(url).netloc or url
    except ValueError:
        # Not a URL at all; the request below says so, inside the net.
        host = url

    def _attempt():
        req = new_request(
            url, headers={"User-Agent": f"{PROGRAM_NAME}/{PROGRAM_VERSION}"},
        )
        with atomic_write(dest, "wb") as fh:
            with opener.open(req) as resp:
                total = declared_length(resp)
                downloaded = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    fh.write(chunk)
                    downloaded += len(chunk)
                    draw_bytes_bar(downloaded, total, noun="downloaded")
                # Inside atomic_write, so a short answer takes the
                # temporary with it instead of publishing part of an
                # archive as the whole of one.
                require_complete_body(downloaded, total)
        clear_bar()
        log_info(f"Finished downloading ({fmt_size(downloaded)}).")

    try:
        retry_http(_attempt, what=f"Downloading {url}",
                   max_retries=max_retries, retry_delay=retry_delay)
    except KeyboardInterrupt:
        clear_bar()
        raise
    except NETWORK_ERRORS as exc:
        clear_bar()
        # retry_http re-raises deterministic failures (and the last transient
        # one once retries are spent); translate them into a meaningful error
        # instead of leaking a raw SSL/HTTP error to the user.
        if isinstance(exc, RedirectRefused) and exc.plaintext:
            raise RuntimeError(
                f"Cannot download {url}: {exc} If you trust this source "
                f"and the network path to it, re-run with "
                f"'--allow-insecure' to follow the redirect."
            ) from exc
        if isinstance(exc, urllib.error.URLError):
            # An untrusted/expired/self-signed certificate.
            if not insecure and is_cert_verification_error(exc):
                raise RuntimeError(certificate_error_msg(host)) from exc
            # A plaintext-HTTP reply to an https:// URL.
            if is_plaintext_http_tls_error(exc):
                raise RuntimeError(
                    f"The URL '{url}' uses HTTPS, but the server at "
                    f"'{host}' responded over plain HTTP (no TLS). If you "
                    f"trust this source, retry with the same URL using the "
                    f"'http://' scheme instead."
                ) from exc
            # An HTTP client error (4xx): the URL or request is wrong.
            if (isinstance(exc, urllib.error.HTTPError)
                    and 400 <= exc.code < 500
                    and exc.code not in (408, 429)):
                raise RuntimeError(
                    f"Cannot download {url}: HTTP {exc.code} {exc.reason}"
                ) from exc
        raise RuntimeError(f"Cannot download {url}: {exc}") from exc
