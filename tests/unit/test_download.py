# Tests for proot_distro.helpers.download — the shared plaintext-HTTP TLS
# error classifier and download_file's handling of an https:// URL that is
# actually served over plain HTTP (urlopen mocked; no network).

import ssl

import urllib.error
import urllib.request

import pytest

from proot_distro.helpers import download


def _ssl_error(reason: str) -> ssl.SSLError:
    err = ssl.SSLError(1, f"[SSL: {reason}] {reason.lower()}")
    err.reason = reason
    return err


# ----- is_plaintext_http_tls_error ----------------------------------------

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


# ----- download_file plaintext detection ----------------------------------

def test_download_file_plaintext_https_meaningful_error(tmp_path, monkeypatch):
    # An https:// URL served by a plaintext-HTTP server must fail fast with a
    # meaningful error (mentioning plain HTTP and the http:// remedy), not a
    # raw SSL error after exhausting all retries.
    calls = []

    def fake_urlopen(req, *a, **k):
        calls.append(req.full_url)
        raise urllib.error.URLError(_ssl_error("WRONG_VERSION_NUMBER"))

    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)
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
