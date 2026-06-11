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
#     we subclass it to drop the header when the host changes.
#   - Token-exchange flow: PD_DOCKER_AUTH (username:password) is the
#     single auth contract; the registry's WWW-Authenticate header
#     tells us where to redeem it for a Bearer token.

import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.constants import PROGRAM_NAME, PROGRAM_VERSION
from proot_distro.helpers.download import (
    certificate_error_msg,
    insecure_ssl_context,
    is_cert_verification_error,
    is_plaintext_http_tls_error,
)


REGISTRY_URL = "https://registry-1.docker.io"
AUTH_URL = "https://auth.docker.io/token"


def _ua() -> dict:
    return {"User-Agent": f"{PROGRAM_NAME}/{PROGRAM_VERSION}"}


class AuthStrippingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Strip the Authorization header when following a cross-host redirect.

    Docker Hub blob endpoints redirect to CDN pre-signed URLs. Those CDN
    hosts return HTTP 400 when they receive a Bearer token. Python's
    default redirect handler forwards all headers unchanged, so we
    override it to drop Authorization whenever the redirect target
    host differs from the source host.
    """

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        new_req = super().redirect_request(req, fp, code, msg, headers, newurl)
        if new_req is None:
            return None
        orig_host = urllib.parse.urlparse(req.full_url).netloc
        new_host = urllib.parse.urlparse(newurl).netloc
        if orig_host != new_host:
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
    except (urllib.error.URLError, OSError):
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
        with urllib.request.urlopen(req) as resp:
            data = json.loads(resp.read())
        token = data.get("token") or data.get("access_token", "")
        return token, REGISTRY_URL

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
            with op.open(probe_req) as resp:
                resp.read()
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
            with op.open(token_req) as resp:
                data = json.loads(resp.read())
            token = data.get("token") or data.get("access_token", "")
            return token, base
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
