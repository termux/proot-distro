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

# Architecture: Registry HTTP plumbing used by both pull and push.
# Three concerns live here:
#
#   - User-Agent header generation (so registries can spot us).
#   - Authorization-stripping redirect handler — Docker Hub blob URLs
#     redirect to CDN hosts that reject Bearer tokens with HTTP 400.
#     Python's default redirect handler keeps headers across hops, so
#     we subclass it to drop the header when the origin changes.
#   - Token-exchange flow: PD_DOCKER_AUTH (username:password) is the
#     single auth contract; the registry's WWW-Authenticate header
#     tells us where to redeem it for a Bearer token.
#
# A credential goes only where it was addressed. same_auth_origin() is
# the one rule for that: the redirect handler asks it before carrying a
# header on to the next hop, and push asks it before carrying the Bearer
# token to the upload Location a registry hands back. The token exchange
# is the other side of the same coin -- PD_DOCKER_AUTH is sent as Basic
# to whatever realm the registry's challenge names, since that is how
# the protocol works (Docker Hub's realm is auth.docker.io, GitLab's is
# gitlab.com for registry.gitlab.com, so the host is legitimately the
# registry's to choose), but the realm has to be an https:// URL, or an
# http:// one under --allow-insecure. The default opener speaks file://,
# ftp:// and data: too, and a plaintext realm is a password on the wire
# without the user having asked for plaintext anything.
#
# Everything a registry says about itself arrives here first, and none of
# it is this program's to trust: how many bytes a metadata response holds
# is the server's choice, and so is whether the body is JSON at all or an
# object of the shape the caller is about to subscript. Bodies are read
# through a ceiling (MAX_METADATA_BYTES) and decoded through
# decode_json_object(), which turns a malformed or wrongly-shaped answer
# into a RuntimeError -- the one exception type every command handler
# already reports cleanly. A registry must not be able to end a command
# in a traceback, and an --allow-insecure MITM or a hostile mirror must
# not be able to end it in an allocation the size of the response.

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.constants import PROGRAM_NAME, PROGRAM_VERSION
from proot_distro.helpers.download import (
    NETWORK_ERRORS,
    certificate_error_msg,
    declared_length,
    insecure_ssl_context,
    is_cert_verification_error,
    is_plaintext_http_tls_error,
    require_complete_body,
    retry_http,
)


REGISTRY_URL = "https://registry-1.docker.io"
AUTH_URL = "https://auth.docker.io/token"

# How large a metadata response may be before it is refused. Manifests,
# manifest indexes, image configs, token grants and search pages are all
# small documents -- a fat multi-arch index is tens of kilobytes -- and
# nothing here streams, so every one of them is held whole in memory.
# 16 MiB is orders of magnitude above any real one and still bounded,
# the same ceiling install_local puts on the JSON inside an OCI archive.
# Layer blobs are not metadata: they stream to disk and are bounded by
# their own digest, not by this.
MAX_METADATA_BYTES = 16 * 1024 * 1024


def _ua() -> dict:
    return {"User-Agent": f"{PROGRAM_NAME}/{PROGRAM_VERSION}"}


_DEFAULT_PORTS = {"http": 80, "https": 443}


def same_auth_origin(from_url: str, to_url: str) -> bool:
    """Whether a credential sent with *from_url* may also go to *to_url*.

    An Authorization header is addressed to an origin -- scheme, host and
    port -- and carrying it to another is handing the secret to whoever
    answers there. The comparison used to be of the two netlocs, which
    let a same-host redirect (or upload Location) that changed only the
    scheme keep the header: https://reg.example -> http://reg.example
    put the Bearer token, or the Basic password behind it, on the wire
    in clear. The one scheme change allowed is an upgrade to HTTPS on the
    default ports, which reaches the same server more securely; an
    explicit port has to match the other side's default, since a port
    the URL leaves out is the scheme's.

    A URL that cannot be read -- a port that is not a number, no host at
    all -- is not the same origin as anything.
    """
    try:
        src = urllib.parse.urlsplit(from_url)
        dst = urllib.parse.urlsplit(to_url)
        src_host, dst_host = src.hostname, dst.hostname
        src_port, dst_port = src.port, dst.port
    except ValueError:
        return False
    if not src_host or src_host != dst_host:
        return False
    if src.scheme == dst.scheme:
        default = _DEFAULT_PORTS.get(src.scheme)
        return (src_port or default) == (dst_port or default)
    return (
        src.scheme == "http" and dst.scheme == "https"
        and src_port in (None, 80) and dst_port in (None, 443)
    )


class AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip the Authorization header when a redirect leaves the origin.

    Docker Hub blob endpoints redirect to CDN pre-signed URLs. Those CDN
    hosts return HTTP 400 when they receive a Bearer token. Python's
    default redirect handler forwards all headers unchanged, so we
    override it to drop Authorization whenever the redirect target is
    not the origin the request was addressed to (same_auth_origin).
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        if not same_auth_origin(req.full_url, newurl):
            new_req.headers.pop("Authorization", None)
        return new_req


def _build_opener(insecure: bool):
    """Build an opener that strips Auth across hosts.

    The *insecure* variant additionally installs an HTTPS handler whose SSL
    context skips certificate verification, so HTTPS endpoints presenting an
    untrusted certificate can be reached under ``--allow-insecure``.
    """
    handlers = [AuthStrippingRedirectHandler]
    if insecure:
        handlers.append(
            urllib.request.HTTPSHandler(context=insecure_ssl_context())
        )
    return urllib.request.build_opener(*handlers)


_verified_opener = _build_opener(False)
_insecure_opener = None


def opener(insecure: bool = False):
    """Return a shared opener; the insecure variant skips TLS cert checks."""
    global _insecure_opener
    if not insecure:
        return _verified_opener
    if _insecure_opener is None:
        _insecure_opener = _build_opener(True)
    return _insecure_opener


def auth_opener():
    """Return the shared (certificate-verifying) opener that strips Auth."""
    return _verified_opener


def _request_body(open_fn, req, what: str, limit: int = None) -> bytes:
    """Open *req* via *open_fn* and return the response body, up to *limit*.

    Transient network failures are retried (same policy as the URL
    downloader). HTTP errors — including the expected 401 that carries the
    Bearer challenge — and deterministic TLS failures are not retried; they
    propagate to the caller, which knows how to handle them.

    The body is metadata, held whole in memory by every caller, and how
    much of it there is is the server's choice — a Content-Length says
    nothing, since the bytes are what arrive. So one byte more than the
    limit is read and its presence is the refusal, which is a
    RuntimeError and therefore not retried: the answer would be the same
    every time.
    """
    cap = MAX_METADATA_BYTES if limit is None else limit

    def _attempt():
        with open_fn(req) as resp:
            data = resp.read(cap + 1)
            declared = declared_length(resp)
        if len(data) > cap:
            raise RuntimeError(
                f"{what}: the registry's response is larger than "
                f"{cap} bytes; refusing to read it."
            )
        # After the cap, never before it: a body larger than the cap is
        # short of its own Content-Length by construction, and that is
        # the refusal above rather than a truncated answer.
        require_complete_body(len(data), declared, what)
        return data
    return retry_http(_attempt, what=what)


def _grant_token(data: dict, what: str) -> str:
    """Pull the Bearer token out of a token-endpoint grant.

    The value goes straight into an Authorization header, so it has to
    be a string: a grant answering with a number or a nested object
    would otherwise be formatted into the header and sent. An empty
    grant is legitimate — a wide-open registry needs no token — and is
    the one non-string this accepts.
    """
    token = data.get("token") or data.get("access_token", "")
    if not token:
        return ""
    if not isinstance(token, str):
        raise RuntimeError(
            f"{what}: the registry's token grant is not a string."
        )
    return token


def decode_json_object(body: bytes, what: str) -> dict:
    """Parse *body* as a JSON object, or raise RuntimeError saying so.

    Every registry response this program reads is a JSON object whose
    members the caller goes on to subscript. A body that is not JSON at
    all, or that decodes to a list or a string, used to surface as a
    ValueError or an AttributeError out of the middle of the pull —
    a traceback, since no command handler catches those. RuntimeError is
    the type they all already report.
    """
    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            f"{what}: the registry returned a malformed response "
            f"(not valid JSON)."
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(
            f"{what}: the registry returned an unexpected response "
            f"(not a JSON object)."
        )
    return data


def registry_base_url(registry: str, insecure: bool = False) -> str:
    """Return the base URL for *registry* (empty string ⇒ Docker Hub).

    HTTPS is used by default. When *insecure* is set the custom registry
    is addressed over plain HTTP — the opt-in behaviour behind the
    install command's ``--allow-insecure``. Docker Hub (empty registry)
    is always served over HTTPS and ignores *insecure*.
    """
    if not registry:
        return REGISTRY_URL
    scheme = "http" if insecure else "https"
    return f"{scheme}://{registry}"


def insecure_registry_msg(registry: str) -> str:
    """Return the error shown when an HTTPS-only pull hits an HTTP registry."""
    return (
        f"Registry '{registry}' is served over plain HTTP, not HTTPS. "
        f"proot-distro enforces TLS by default. If you trust this registry "
        f"and the network path to it, re-run with '--allow-insecure' to "
        f"permit the unencrypted connection."
    )


def _http_registry_reachable(registry: str, timeout: float = 6.0) -> bool:
    """Return True if *registry* answers a /v2/ probe over plaintext HTTP.

    Fallback used on the error path when the TLS error itself is not a
    conclusive plaintext signal (see is_plaintext_http_tls_error), to
    decide whether an HTTPS failure is because the registry is HTTP-only
    (so we can point the user at ``--allow-insecure``) rather than simply
    unreachable. Any HTTP-level response — including 401/404 — confirms the
    host speaks HTTP on that endpoint.
    """
    req = urllib.request.Request(f"http://{registry}/v2/", headers=_ua())
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            resp.read(64)
        return True
    except urllib.error.HTTPError:
        return True
    except NETWORK_ERRORS:
        return False


def auth_denied_msg(image_ref: str, code: int) -> str:
    """Return a descriptive error string for 401/403 registry responses."""
    if os.environ.get("PD_DOCKER_AUTH"):
        return (
            f"Access denied to '{image_ref}' (HTTP {code}). "
            f"Check that PD_DOCKER_AUTH=username:password is correct "
            f"and the account has pull access to the image."
        )
    return (
        f"Unauthorized: '{image_ref}' does not exist or is a private image. "
        f"Set PD_DOCKER_AUTH=username:password to authenticate."
    )


def push_denied_msg(image_ref: str, code: int) -> str:
    """Return a context-sensitive error string for 401/403 on push."""
    if os.environ.get("PD_DOCKER_AUTH"):
        return (
            f"Push denied for '{image_ref}' (HTTP {code}). "
            f"Check that PD_DOCKER_AUTH=username:password is correct "
            f"and the account has push access to the repository."
        )
    return (
        f"Push denied for '{image_ref}' (HTTP {code}). "
        f"Set PD_DOCKER_AUTH=username:password to authenticate, or, "
        f"for self-hosted registries that allow anonymous push, check "
        f"the registry configuration."
    )


_CHALLENGE_PARAM_RE = re.compile(
    r'(\w+)\s*=\s*(?:"([^"]*)"|([^",\s]+))'
)


def _parse_bearer_challenge(header_value: str) -> dict:
    """Return the key=value pairs from a Bearer WWW-Authenticate header.

    Per RFC 7235 each auth-param's value may be either a quoted-string
    or a bare token. Practical registries (Docker Hub, GHCR, ECR) quote
    everything, but the spec permits e.g.
        Bearer realm=https://auth.example/token,service=svc
    on a self-hosted registry. We accept both forms so the probe still
    works against spec-compliant minimal implementations.
    """
    return {
        key: (quoted if quoted else bare)
        for key, quoted, bare in _CHALLENGE_PARAM_RE.findall(header_value)
    }


def env_basic_auth() -> str:
    """Return a Basic auth header value from PD_DOCKER_AUTH, or ''.

    Accepts 'username:password' — the colon is the required separator.
    Returns '' when the variable is unset; raises RuntimeError when the
    variable is set but contains no colon (wrong format).
    """
    raw = os.environ.get("PD_DOCKER_AUTH", "")
    if not raw:
        return ""
    if ":" not in raw:
        raise RuntimeError(
            "PD_DOCKER_AUTH must be in 'username:password' format "
            "(e.g. 'myuser:mypassword' or 'myuser:ghp_xxx'). "
            "A bare token without a username cannot be used — registry "
            "auth requires a token exchange with Basic credentials."
        )
    return "Basic " + base64.b64encode(raw.encode()).decode()


def _require_usable_realm(realm: str, registry: str, insecure: bool) -> None:
    """Refuse a Bearer realm this program must not send credentials to.

    The realm is a URL out of the registry's own challenge, and the token
    request that follows carries PD_DOCKER_AUTH as a Basic header. Which
    *host* it names is the registry's to choose -- the protocol puts the
    token service wherever the operator likes, and Docker Hub, GitLab and
    the rest all put it somewhere other than the registry -- but the
    scheme is not. Only https:// keeps the password off the wire, and
    http:// is what --allow-insecure means, for the registry and for its
    realm alike. Anything else is refused outright: the opener the
    request goes through speaks file://, ftp:// and data: as well, so an
    unchecked realm was a challenge's way of having this process open
    whatever it liked and parse the result as a token grant.
    """
    parts = urllib.parse.urlsplit(realm)
    scheme = parts.scheme.lower()
    if scheme == "https" and parts.hostname:
        return
    if scheme == "http" and parts.hostname:
        if insecure:
            return
        raise RuntimeError(
            f"Registry '{registry}' directs authentication to '{realm}', "
            f"which is served over plain HTTP, not HTTPS. proot-distro "
            f"enforces TLS by default and will not send credentials in "
            f"clear. If you trust this registry and the network path to "
            f"it, re-run with '--allow-insecure' to permit the "
            f"unencrypted connection."
        )
    raise RuntimeError(
        f"Registry '{registry}' directs authentication to '{realm}', "
        f"which is not an https:// URL (or an http:// one under "
        f"'--allow-insecure'). Refusing to send credentials there."
    )


def get_auth_token(
    repo: str, registry: str = "", actions: str = "pull",
    insecure: bool = False,
) -> tuple:
    """Resolve a registry's base URL and an OAuth2 token for *repo*.

    Returns ``(token, base_url)`` where *base_url* is the resolved
    ``scheme://registry`` that every subsequent request for this image must
    use. *token* is empty for wide-open registries.

    `actions` is a comma-separated list of registry actions to request,
    such as 'pull' (default), 'push', or 'pull,push'. The push flow
    needs 'pull,push'; the pull flow uses the default 'pull'.

    When PD_DOCKER_AUTH is set, its 'username:password' value is
    forwarded as HTTP Basic auth to the registry's token endpoint,
    enabling access to private images. PD_DOCKER_AUTH must always
    contain a colon separating the username from the password/PAT.

    Without PD_DOCKER_AUTH Docker Hub uses its well-known auth endpoint for
    anonymous requests (always HTTPS). For any other registry the scheme and
    Bearer realm are discovered with a single /v2/ probe:

      * HTTPS is tried first — even under *insecure*, so a registry serving
        an untrusted certificate is reached (cert verification is skipped
        only when *insecure* is set).
      * A certificate failure raises a RuntimeError pointing at
        ``--allow-insecure`` (unless already insecure).
      * A registry that answers the HTTPS probe with plaintext is HTTP-only:
        under *insecure* it is retried over http://; otherwise a RuntimeError
        points the user at ``--allow-insecure``.
      * The realm the challenge names must be an https:// URL -- or an
        http:// one under *insecure* -- before anything is sent to it
        (see _require_usable_realm).
    """
    basic_auth = env_basic_auth()

    if not registry:
        url = (
            f"{AUTH_URL}?service=registry.docker.io"
            f"&scope=repository:{repo}:{actions}"
        )
        req = urllib.request.Request(url, headers=_ua())
        if basic_auth:
            req.add_header("Authorization", basic_auth)
        what = f"Authenticating {repo}"
        # Through the auth-stripping opener, never urlopen(): the default
        # redirect handler carries the Basic header to whatever host a
        # redirect names, and this is the one request that has the user's
        # password in it. Docker Hub is always verified HTTPS, so it is
        # the verifying opener whatever `insecure` says.
        data = decode_json_object(
            _request_body(auth_opener().open, req, what), what,
        )
        return _grant_token(data, what), REGISTRY_URL

    # Custom registry: probe /v2/ to resolve the scheme and discover the
    # Bearer realm. Registries serving public images still require this dance —
    # they answer 401 to unauthenticated requests and embed the token endpoint
    # in the challenge.
    op = opener(insecure)
    scheme = "https"
    while True:
        base = f"{scheme}://{registry}"
        probe_req = urllib.request.Request(f"{base}/v2/", headers=_ua())
        try:
            _request_body(op.open, probe_req, f"Probing {base}/v2/")
            return "", base  # registry is wide open; no token required
        except urllib.error.HTTPError as exc:
            if exc.code != 401:
                raise
            www_auth = exc.headers.get("WWW-Authenticate", "")
            if not www_auth.lower().startswith("bearer "):
                return "", base
            params = _parse_bearer_challenge(www_auth.split(" ", 1)[1])
            realm = params.get("realm", "")
            if not realm:
                return "", base
            _require_usable_realm(realm, registry, insecure)
            service = params.get("service", "")
            qs_parts = []
            if service:
                qs_parts.append(
                    f"service={urllib.parse.quote(service, safe='')}"
                )
            qs_parts.append(f"scope=repository:{repo}:{actions}")
            sep = "&" if "?" in realm else "?"
            token_req = urllib.request.Request(
                f"{realm}{sep}{'&'.join(qs_parts)}", headers=_ua()
            )
            if basic_auth:
                token_req.add_header("Authorization", basic_auth)
            what = "Requesting auth token"
            data = decode_json_object(
                _request_body(op.open, token_req, what), what,
            )
            return _grant_token(data, what), base
        except urllib.error.URLError as exc:
            # The server speaks TLS but its certificate is untrusted. Only
            # reachable when enforcing HTTPS (the insecure opener skips
            # verification, so no cert error occurs there).
            if not insecure and is_cert_verification_error(exc):
                raise RuntimeError(certificate_error_msg(registry)) from exc
            # The registry answered the HTTPS probe with plaintext (or only
            # responds over plain HTTP): it is HTTP-only. Two signals,
            # cheapest first — the handshake error itself (WRONG_VERSION_NUMBER
            # and friends), else an active HTTP re-probe.
            if scheme == "https" and (
                is_plaintext_http_tls_error(exc)
                or _http_registry_reachable(registry)
            ):
                if insecure:
                    scheme = "http"  # retry the whole probe over plain HTTP
                    continue
                raise RuntimeError(insecure_registry_msg(registry)) from exc
            raise


def auth_note(prefix_space: bool = True) -> str:
    """Return ' (user credentials)' or ' (anonymous)' for log lines."""
    head = " " if prefix_space else ""
    if os.environ.get("PD_DOCKER_AUTH"):
        return f"{head}(user credentials)"
    return f"{head}(anonymous)"
