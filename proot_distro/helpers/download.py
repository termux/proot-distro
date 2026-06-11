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

import hashlib
import os
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.atomic import atomic_replace
from proot_distro.constants import PROGRAM_NAME, PROGRAM_VERSION
from proot_distro.message import msg, log_info, log_error
from proot_distro.progress import clear_bar, draw_bytes_bar, fmt_size


__all__ = (
    "certificate_error_msg",
    "download_file",
    "insecure_ssl_context",
    "is_cert_verification_error",
    "is_plaintext_http_tls_error",
    "sha256_file",
)


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

    HTTPS certificates are verified by default. When *insecure* is set the
    download proceeds even if the server presents an untrusted/expired/
    self-signed certificate — the opt-in behaviour behind the install
    command's ``--allow-insecure``.
    """
    req = urllib.request.Request(
        url, headers={"User-Agent": f"{PROGRAM_NAME}/{PROGRAM_VERSION}"},
    )
    context = insecure_ssl_context() if insecure else None
    for attempt in range(max_retries):
        try:
            with atomic_replace(dest) as tmp:
                with urllib.request.urlopen(req, context=context) as resp, \
                        open(tmp, "wb") as fh:
                    total = int(resp.headers.get("Content-Length", 0))
                    downloaded = 0
                    while True:
                        chunk = resp.read(65536)
                        if not chunk:
                            break
                        fh.write(chunk)
                        downloaded += len(chunk)
                        draw_bytes_bar(downloaded, total, noun="downloaded")
            clear_bar()
            log_info(f"Finished downloading ({fmt_size(downloaded)}).")
            return
        except KeyboardInterrupt:
            clear_bar()
            raise
        except (urllib.error.URLError, OSError) as exc:
            clear_bar()
            # Some failures are deterministic — retrying cannot fix them, so
            # surface a meaningful error immediately instead of looping with
            # delays and then printing a raw error after exhausting every retry.
            #
            # An HTTP client error (4xx) means the URL or request is wrong: a
            # 404 will not become a 200 on retry. 408 (Request Timeout) and 429
            # (Too Many Requests) are the standard "retry later" codes, so those
            # alone fall through to the retry loop.
            if (isinstance(exc, urllib.error.HTTPError)
                    and 400 <= exc.code < 500
                    and exc.code not in (408, 429)):
                raise RuntimeError(
                    f"Cannot download {url}: HTTP {exc.code} {exc.reason}"
                ) from exc
            if isinstance(exc, urllib.error.URLError):
                host = urllib.parse.urlparse(url).netloc or url
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
            if attempt < max_retries - 1:
                time.sleep(retry_delay)
                continue
            msg()
            log_error("Download failure, please check your network connection.")
            raise RuntimeError(f"Cannot download {url}: {exc}") from exc
