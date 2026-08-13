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

# Architecture: Docker Hub's repository search — the endpoint behind
# `docker search`. Three things make this module its own concern rather
# than part of the pull/push plumbing:
#
#   - It is Docker Hub only. Searching is not part of the OCI
#     distribution spec; a registry serving /v2/ says nothing about
#     whether it can be searched, and most cannot. Every other module
#     here talks to whatever registry the reference names, so the
#     hardcoded index.docker.io host lives only in this file.
#   - No token exchange. The endpoint answers anonymously, and
#     PD_DOCKER_AUTH is deliberately *not* forwarded: Hub ignores Basic
#     credentials here (a bogus user:password still returns HTTP 200
#     with the same public results), so sending them would hand the
#     user's registry password to a third endpoint for nothing. Private
#     repositories therefore never appear in results.
#   - The response is a JSON document written by other users. Every
#     field is re-typed and re-validated below before it leaves this
#     module: a repository name that does not match Docker's own name
#     grammar is dropped entirely (nothing could install it anyway,
#     and the name is printed bare under `search --quiet`), and the
#     free-text description is collapsed to a single line and escaped
#     with quote_path so no remote string can repaint the terminal.
#
# Hub caps `n` at 100 per request (asking for 150 yields page_size 100),
# so a larger limit is collected by walking `page` with a *constant*
# page size — the page number is an offset multiplier, so shrinking `n`
# on the last request would re-fetch results already held.

import json
import re
import urllib.error
import urllib.parse
import urllib.request

from proot_distro.message import quote_path
from proot_distro.helpers.download import (
    certificate_error_msg,
    is_cert_verification_error,
    retry_http,
)
from proot_distro.helpers.docker.transport import _ua, opener


SEARCH_URL = "https://index.docker.io/v1/search"

# Docker Hub's own ceiling on results per request. Anything above this
# is silently trimmed by the server, so it also sets the page size.
SEARCH_PAGE_MAX = 100

# What `search` asks for when the user names no limit (same as docker's).
SEARCH_DEFAULT_LIMIT = 25

# Ceiling on the caller's limit. Every SEARCH_PAGE_MAX results cost one
# more request, so an unbounded limit would turn a typo into a flood.
SEARCH_LIMIT_MAX = 1000

# Search is interactive and produces no output until it completes, so a
# stalled connection must not hang the command indefinitely.
_TIMEOUT = 30.0

# Docker's repository-name grammar (distribution/reference): lowercase
# alphanumeric path components, separated by '.', '_', '__' or runs of
# '-', joined by '/'. A name failing this is not addressable by any
# client, so such an entry is not a result worth returning.
_NAME_COMPONENT = r"[a-z0-9]+(?:(?:[._]|__|[-]*)[a-z0-9]+)*"
_REPO_NAME_RE = re.compile(rf"^{_NAME_COMPONENT}(?:/{_NAME_COMPONENT})*$")
_NAME_MAX = 255


def _count(value) -> int:
    """Return *value* as a non-negative int, or 0 if it is not one.

    Star and pull counts come off the wire, so anything but a plain
    number — including a bool, which int() would happily accept — is
    treated as "unknown" rather than trusted into a format string.
    """
    if isinstance(value, bool):
        return 0
    if isinstance(value, str):
        value = value.strip()
    try:
        number = int(value)
    except (TypeError, ValueError, OverflowError):
        return 0
    return number if number > 0 else 0


def _normalize(entry) -> dict:
    """Return one search hit as a plain dict, or None when unusable.

    This is the trust boundary: past this point the name is known to be
    a legal repository reference and the description is known to be a
    single line of printable text.
    """
    if not isinstance(entry, dict):
        return None

    name = entry.get("name")
    if not isinstance(name, str) or len(name) > _NAME_MAX:
        return None
    if not _REPO_NAME_RE.match(name):
        return None

    description = entry.get("description")
    if not isinstance(description, str):
        description = ""
    # Descriptions carry newlines and runs of whitespace that would break
    # a table row; collapse them first, then escape whatever control
    # characters are left (quote_path renders ESC as '\e', never emits it).
    description = quote_path(" ".join(description.split()))

    return {
        "name": name,
        "description": description,
        "stars": _count(entry.get("star_count")),
        "pulls": _count(entry.get("pull_count")),
        "official": bool(entry.get("is_official")),
        "automated": bool(entry.get("is_automated")),
    }


def _fetch_page(query: str, page: int, page_size: int) -> dict:
    """Fetch one page of results and return the decoded JSON object."""
    params = urllib.parse.urlencode(
        {"q": query, "n": page_size, "page": page}
    )
    req = urllib.request.Request(f"{SEARCH_URL}?{params}", headers=_ua())

    def _attempt():
        with opener().open(req, timeout=_TIMEOUT) as resp:
            return resp.read()

    try:
        body = retry_http(_attempt, what=f"Searching for '{query}'")
    except urllib.error.HTTPError as exc:
        raise RuntimeError(
            f"Docker Hub refused the search request: "
            f"HTTP {exc.code} {exc.reason}."
        ) from exc
    except urllib.error.URLError as exc:
        if is_cert_verification_error(exc):
            raise RuntimeError(
                certificate_error_msg("index.docker.io")
            ) from exc
        raise

    try:
        data = json.loads(body)
    except (ValueError, UnicodeDecodeError) as exc:
        raise RuntimeError(
            "Docker Hub returned a malformed search response."
        ) from exc
    if not isinstance(data, dict):
        raise RuntimeError(
            "Docker Hub returned an unexpected search response."
        )
    return data


def search_images(query: str, limit: int = SEARCH_DEFAULT_LIMIT) -> tuple:
    """Search Docker Hub for *query*.

    Returns ``(results, total)`` where *results* is a list of at most
    *limit* hit dicts (``name``, ``description``, ``stars``, ``pulls``,
    ``official``, ``automated``) in the order Hub ranked them, and
    *total* is Hub's own count of matching repositories — which is
    normally far larger than what was asked for, and is what the
    command reports as "showing N of M".

    Only public repositories are searched; see the module comment on
    why credentials are not forwarded.
    """
    query = query.strip()
    if not query:
        raise RuntimeError("The search query is empty.")

    limit = max(1, min(int(limit), SEARCH_LIMIT_MAX))
    page_size = min(limit, SEARCH_PAGE_MAX)
    max_pages = -(-limit // page_size)  # ceil, so ≥1 page is always tried

    results = []
    seen = set()
    total = 0

    for page in range(1, max_pages + 1):
        data = _fetch_page(query, page, page_size)
        raw = data.get("results")
        if not isinstance(raw, list):
            raise RuntimeError(
                "Docker Hub returned a malformed search response."
            )
        total = max(total, _count(data.get("num_results")))

        for entry in raw:
            hit = _normalize(entry)
            if hit is None or hit["name"] in seen:
                continue
            seen.add(hit["name"])
            results.append(hit)
            if len(results) >= limit:
                break

        if len(results) >= limit:
            break
        # A short page is the last one Hub has to give: asking for the
        # next would only cost a request that returns nothing.
        if len(raw) < page_size:
            break
        pages_available = _count(data.get("num_pages"))
        if pages_available and page >= pages_available:
            break

    return results[:limit], max(total, len(results))
