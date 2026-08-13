# Opt-in live tests: real Docker Hub searches. Skipped unless RUN_LIVE_TESTS=1.
#
#   RUN_LIVE_TESTS=1 python -m pytest tests/live -q
#
# These hit the network and depend on Docker Hub availability and on the
# shape of its /v1/search response, which no other test can verify.

import re

import pytest

from proot_distro.helpers.docker import search as hub

pytestmark = pytest.mark.live


def test_live_search_finds_the_official_image():
    results, total = hub.search_images("nginx", limit=25)
    assert 0 < len(results) <= 25
    assert total >= len(results)
    names = [hit["name"] for hit in results]
    assert "nginx" in names
    official = next(hit for hit in results if hit["name"] == "nginx")
    assert official["official"] is True
    assert official["stars"] > 0
    assert official["pulls"] > 0
    assert official["description"]


def test_live_search_names_are_installable_references():
    results, _ = hub.search_images("alpine", limit=25)
    for hit in results:
        # Whatever Hub returns, what leaves the helper must be a legal
        # reference — `search --quiet` prints these bare for piping.
        assert re.match(hub._REPO_NAME_RE, hit["name"])
        assert "\x1b" not in hit["description"]


def test_live_search_pages_past_hubs_per_request_cap():
    limit = hub.SEARCH_PAGE_MAX + 50
    results, _ = hub.search_images("linux", limit=limit)
    assert len(results) == limit
    # Paging must not re-serve rows already held: a constant page size
    # keeps the server-side offsets aligned.
    assert len({hit["name"] for hit in results}) == limit


def test_live_search_without_matches_is_empty_not_an_error():
    # A single nonsense token: Hub tokenises a query, so anything with
    # separators ('no-such-image') still matches on its parts.
    results, total = hub.search_images("zzzqqqxxxnotathing123", limit=10)
    assert results == []
    assert total == 0
