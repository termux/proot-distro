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
# multi-arch manifest index from the registry so the search table can
# list which CPU architectures each hit ships.

import json
import urllib.parse
import urllib.request

from proot_distro.helpers.download import retry_http
from proot_distro.helpers.docker.media import (
    DOCKER_MANIFEST_LIST_MEDIA,
    DOCKER_MANIFEST_MEDIA,
    OCI_INDEX_MEDIA,
    OCI_MANIFEST_MEDIA,
)
from proot_distro.helpers.docker.refs import DOCKER_TO_ARCH, parse_image_ref
from proot_distro.helpers.docker.transport import _ua, get_auth_token, opener


HUB_SEARCH_URL = "https://hub.docker.com/v2/search/repositories/"

# Docker Hub caps a single search page at this many results; larger
# limits are fetched by walking `page` until the limit is satisfied or
# the API stops answering.
MAX_PAGE_SIZE = 100

# Media types that describe a multi-architecture image index — the only
# manifest kind whose entries carry a platform we can read an arch from.
_INDEX_MEDIA_TYPES = frozenset({DOCKER_MANIFEST_LIST_MEDIA, OCI_INDEX_MEDIA})

# The same Accept list as the pull pipeline, so the registry hands us an
# index whenever the image ships one (single-arch images answer with their
# plain manifest instead).
_INDEX_ACCEPT = ", ".join([
    OCI_INDEX_MEDIA,
    DOCKER_MANIFEST_LIST_MEDIA,
    OCI_MANIFEST_MEDIA,
    DOCKER_MANIFEST_MEDIA,
])


def search_images(query: str, limit: int = 100) -> list:
    """Search Docker Hub for repositories matching *query*.

    Returns a list of result dicts carrying the fields `docker search`
    shows — name, description, star_count, is_official, is_automated —
    plus the raw pull_count. Transient network failures are retried
    with the same policy as registry pulls. An empty *query* (after
    stripping) yields an empty list without any network access.

    Docker Hub answers at most `MAX_PAGE_SIZE` repositories per page, so
    a *limit* beyond that walks the `page` parameter until the limit is
    satisfied or the API stops answering.
    """
    query = (query or "").strip()
    if not query:
        return []
    limit = max(1, int(limit))
    results = []
    page = 1
    while len(results) < limit:
        page_size = min(MAX_PAGE_SIZE, limit - len(results))
        params = {"query": query, "page_size": page_size, "page": page}
        url = f"{HUB_SEARCH_URL}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=_ua())

        def _attempt():
            with opener().open(req) as resp:
                return resp.read()

        data = json.loads(retry_http(_attempt, what="Searching Docker Hub"))
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

    Search results are repositories, not tags, so the registry is always
    probed for the 'latest' tag. The multi-arch manifest index is fetched
    and each entry's platform is mapped back to a proot-distro
    architecture name (unknown Docker arch names pass through unchanged).

    An empty list is returned whenever the answer cannot be determined —
    the image is single-architecture (a plain manifest carries no
    platform), the 'latest' tag does not exist, the repository is private,
    or the network failed — so a search table never fails because one
    repository refused to answer.
    """
    registry, repo, _tag = parse_image_ref(image_ref)
    try:
        token, base = get_auth_token(repo, registry, insecure=insecure)
    except Exception:
        return []

    headers = {**_ua(), "Accept": _INDEX_ACCEPT}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = urllib.request.Request(
        f"{base}/v2/{repo}/manifests/latest", headers=headers
    )

    def _attempt():
        with opener(insecure).open(req) as resp:
            return resp.read(), resp.headers.get("Content-Type", "")

    try:
        body, ct = retry_http(
            _attempt, what=f"Fetching manifest for {image_ref}"
        )
    except Exception:
        return []
    try:
        data = json.loads(body)
    except ValueError:
        return []

    ct = ct.split(";")[0].strip() or data.get("mediaType", "")
    if ct not in _INDEX_MEDIA_TYPES or "manifests" not in data:
        return []

    archs = set()
    for entry in data.get("manifests", []) or []:
        platform = entry.get("platform", {}) or {}
        if platform.get("os", "linux") != "linux":
            continue
        docker_arch = platform.get("architecture")
        if not docker_arch:
            continue
        archs.add(DOCKER_TO_ARCH.get(docker_arch, docker_arch))
    return sorted(archs)
