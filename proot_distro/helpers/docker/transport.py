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


_auth_stripping_opener = urllib.request.build_opener(
    AuthStrippingRedirectHandler
)


def auth_opener():
    """Return the shared opener that strips Auth across hosts."""
    return _auth_stripping_opener


def registry_base_url(registry: str) -> str:
    """Return the base URL for *registry* (empty string ⇒ Docker Hub)."""
    return f"https://{registry}" if registry else REGISTRY_URL


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
) -> str:
    """Obtain an OAuth2 token for *repo* with the requested *actions* scope.

    `actions` is a comma-separated list of registry actions to request,
    such as 'pull' (default), 'push', or 'pull,push'. The push flow
    needs 'pull,push'; the pull flow uses the default 'pull'.

    When PD_DOCKER_AUTH is set, its 'username:password' value is
    forwarded as HTTP Basic auth to the registry's token endpoint,
    enabling access to private images. PD_DOCKER_AUTH must always
    contain a colon separating the username from the password/PAT.

    Without PD_DOCKER_AUTH Docker Hub uses its well-known auth
    endpoint for anonymous requests. For any other registry the Bearer
    realm is discovered by probing /v2/ and following the standard OCI
    auth challenge.
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
        return data.get("token") or data.get("access_token", "")

    # Custom registry: probe /v2/ to discover the Bearer realm. Registries
    # serving public images still require this dance — they answer 401
    # to unauthenticated requests and embed the token endpoint in the
    # challenge.
    probe_req = urllib.request.Request(
        f"https://{registry}/v2/", headers=_ua()
    )
    try:
        with urllib.request.urlopen(probe_req) as resp:
            resp.read()
        return ""  # registry is wide open; no token required
    except urllib.error.HTTPError as exc:
        if exc.code != 401:
            raise
        www_auth = exc.headers.get("WWW-Authenticate", "")
        if not www_auth.lower().startswith("bearer "):
            return ""
        params = _parse_bearer_challenge(www_auth.split(" ", 1)[1])
        realm = params.get("realm", "")
        if not realm:
            return ""
        service = params.get("service", "")
        qs_parts = []
        if service:
            qs_parts.append(f"service={urllib.parse.quote(service, safe='')}")
        qs_parts.append(f"scope=repository:{repo}:{actions}")
        sep = "&" if "?" in realm else "?"
        token_req = urllib.request.Request(
            f"{realm}{sep}{'&'.join(qs_parts)}", headers=_ua()
        )
        if basic_auth:
            token_req.add_header("Authorization", basic_auth)
        with urllib.request.urlopen(token_req) as resp:
            data = json.loads(resp.read())
        return data.get("token") or data.get("access_token", "")


def auth_note(prefix_space: bool = True) -> str:
    """Return ' (user credentials)' or ' (anonymous)' for log lines."""
    head = " " if prefix_space else ""
    if os.environ.get("PD_DOCKER_AUTH"):
        return f"{head}(user credentials)"
    return f"{head}(anonymous)"
