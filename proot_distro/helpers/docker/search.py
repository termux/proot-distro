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

# Architecture: Docker Hub repository search, mirroring `docker search`.
# The OCI Distribution specification has no standard search endpoint, so
# search lives against Docker Hub's own REST API (hub.docker.com) rather
# than the registry protocol in pull.py/push.py. Custom registries
# therefore cannot be searched — Hub is the default registry anyway.
#
# image_architectures() is the second half: it reads a repository's
# published tag metadata from the same hub.docker.com API, whose
# images[] list names the OS and CPU architecture of every platform the
# tag ships. One unauthenticated request answers what a registry token
# dance plus a manifest fetch (and, for a single-architecture image, a
# config-blob fetch) used to answer — and it answers for single-arch
# images too, since the metadata lists an architecture per image.

import json
import urllib.parse
import urllib.request

from proot_distro.helpers.download import retry_http
from proot_distro.helpers.docker.refs import (
    ARCH_TO_DOCKER,
    DOCKER_TO_ARCH,
    parse_image_ref,
)
from proot_distro.helpers.docker.transport import _ua, opener


HUB_SEARCH_URL = "https://hub.docker.com/v2/search/repositories/"

# Docker Hub caps a single search page at this many results; larger
# limits are fetched by walking `page` until the limit is satisfied or
# the API stops answering. The API answers HTTP 403 to any page past
# the last one (in practice, past the second page of one hundred), so an
# unbounded walk terminates exactly where a bounded one would.
MAX_PAGE_SIZE = 100

# Published metadata for one repository's tag. A repository with no
# 'latest' tag answers HTTP 404 — exactly the case the caller cannot
# install, since `install <repo>` defaults to :latest.
HUB_TAG_API = "https://hub.docker.com/v2/repositories/{}/tags/latest"


def search_images(query: str, limit: int = None) -> list:
    """Search Docker Hub for repositories matching *query*.

    Returns a list of result dicts carrying the fields `docker search`
    shows — name, description, star_count, is_official, is_automated —
    plus the raw pull_count. Transient network failures are retried
    with the same policy as registry pulls. An empty *query* (after
    stripping) yields an empty list without any network access.

    Docker Hub answers at most `MAX_PAGE_SIZE` repositories per page, so
    a *limit* beyond that walks the `page` parameter until the limit is
    satisfied or the API stops answering. With no *limit* (the default)
    the walk continues until the API stops answering — every repository
    Docker Hub will serve for the query.
    """
    query = (query or "").strip()
    if not query:
        return []
    results = []
    page = 1
    while limit is None or len(results) < limit:
        if limit is None:
            page_size = MAX_PAGE_SIZE
        else:
            page_size = min(MAX_PAGE_SIZE, max(1, int(limit)) - len(results))
        if page_size <= 0:
            break
        params = {"query": query, "page_size": page_size, "page": page}
        url = f"{HUB_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=_ua())

        def _attempt():
            with opener().open(req) as resp:
                return resp.read()

        try:
            data = json.loads(retry_http(_attempt, what="Searching Docker Hub"))
        except Exception:
            # A page past the last one (Docker Hub answers HTTP 403)
            # ends the walk with what we already have. A failure on the
            # first page is a real network problem and must propagate.
            if page == 1:
                raise
            break
        fetched = data.get("results", []) or []
        if not fetched:
            break
        results.extend(
            {
                "name": item.get("repo_name") or item.get("name", "?"),
                "description": (
                    item.get("short_description")
                    or item.get("description")
                    or ""
                ),
                "star_count": item.get("star_count") or 0,
                "is_official": item.get("is_official") or False,
                "is_automated": item.get("is_automated") or False,
                "pull_count": item.get("pull_count") or 0,
            }
            for item in fetched
        )
        if len(fetched) < page_size:
            break
        page += 1
    return results


def image_architectures(image_ref: str, insecure: bool = False) -> list:
    """Return the proot-distro arch names *image_ref*'s latest tag ships.

    Search results are repositories, not tags, so Docker Hub's tag API is
    always asked about 'latest'. Its images[] metadata names the OS and
    CPU architecture of every platform the tag ships — single-architecture
    images included — and Docker arch names are mapped back to
    proot-distro names, unknown ones passing through unchanged. A
    repository with no 'latest' tag answers HTTP 404, which is exactly the
    case the caller cannot install.

    *insecure* is accepted for signature compatibility but ignored:
    hub.docker.com is always reached over verified HTTPS.

    An empty list is returned whenever the answer cannot be determined —
    the 'latest' tag does not exist, the repository is private, the
    network failed, or the registry is rate-limiting — so a search table
    never fails because one repository refused to answer. Each lookup is
    attempted once: a search resolving a page of repositories is
    best-effort by design, and backing off five seconds per hit would
    turn a rate-limited search into a crawl.
    """
    _registry, repo, _tag = parse_image_ref(image_ref)
    req = urllib.request.Request(HUB_TAG_API.format(repo), headers=_ua())

    def _attempt():
        with opener().open(req) as resp:
            return resp.read()

    try:
        data = json.loads(retry_http(
            _attempt, what=f"Fetching tags for {image_ref}",
            max_retries=1,
        ))
    except Exception:
        return []

    archs = set()
    for image in data.get("images", []) or []:
        if image.get("os", "linux") != "linux":
            continue
        docker_arch = image.get("architecture")
        if not docker_arch or docker_arch == "unknown":
            continue
        archs.add(DOCKER_TO_ARCH.get(docker_arch, docker_arch))
    return sorted(archs)


# The CPU architectures proot-distro can install. image_architectures()
# lets unknown Docker arch names (ppc64le, s390x, …) pass through
# unchanged, so a hit that ships only those must not count as installable.
INSTALLABLE_ARCHS = frozenset(ARCH_TO_DOCKER)


def is_installable(architectures: list) -> bool:
    """True when at least one of *architectures* is an arch proot-distro
    can install."""
    return bool(set(architectures) & INSTALLABLE_ARCHS)
