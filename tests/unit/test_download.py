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


# ----- download_file certificate handling ---------------------------------

def test_download_file_cert_error_meaningful(tmp_path, monkeypatch):
    # An https:// URL with an untrusted certificate must fail fast with a
    # meaningful error pointing at --allow-insecure, not a raw SSL error after
    # exhausting all retries.
    calls = []

    def fake_urlopen(req, *a, **k):
        calls.append(req.full_url)
        raise urllib.error.URLError(
            ssl.SSLCertVerificationError(1, "self-signed certificate")
        )

    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)
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


def test_download_file_insecure_passes_unverified_context(tmp_path, monkeypatch):
    # With insecure=True the download is performed with an SSL context that
    # skips verification, so a bad certificate no longer blocks it.
    seen = {}

    def fake_urlopen(req, *a, **k):
        seen["context"] = k.get("context")
        return _FakeDownloadResp(b"payload")

    monkeypatch.setattr(download.urllib.request, "urlopen", fake_urlopen)
    dest = tmp_path / "rootfs.tar"
    download.download_file(
        "https://example.com/rootfs.tar", str(dest), insecure=True,
    )
    ctx = seen["context"]
    assert ctx is not None
    assert ctx.verify_mode == ssl.CERT_NONE
    assert dest.read_bytes() == b"payload"
