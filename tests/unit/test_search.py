# Tests for `proot-distro search` — the Docker Hub search helper and the
# command handler (table / quiet / empty / error paths). Network is mocked.

import io
import json
import urllib.parse
from types import SimpleNamespace

import pytest

from proot_distro.helpers.docker import search as search_mod
from proot_distro.commands import search as search_cmd
from proot_distro.helpers import download


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)


class _FakeResp:
    def __init__(self, payload):
        self._buf = io.BytesIO(json.dumps(payload).encode())

    def read(self, *a):
        return self._buf.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class _FakeOpener:
    def __init__(self, fn):
        self._fn = fn

    def open(self, req, *a, **k):
        return self._fn(req, *a, **k)


def _patch_opener(monkeypatch, fn):
    monkeypatch.setattr(search_mod, "opener",
                        lambda insecure=False: _FakeOpener(fn))


_SAMPLE_RESULTS = [
    {"name": "library/ubuntu", "description": "Ubuntu is a Debian-based "
     "Linux operating system.", "star_count": 15266, "is_official": True,
     "is_automated": False, "pull_count": 123456789},
    {"name": "myuser/ubuntu-lite", "description": "Minimal Ubuntu.",
     "star_count": 12, "is_official": False, "is_automated": True,
     "pull_count": 9000},
]


# ----- helper -------------------------------------------------------------

def test_search_images_encodes_query_and_limit(monkeypatch):
    captured = {}

    def fake_open(req, *a, **k):
        captured["url"] = req.full_url
        return _FakeResp({"count": 1, "results": _SAMPLE_RESULTS})

    _patch_opener(monkeypatch, fake_open)
    results = search_mod.search_images("ubuntu", limit=10)
    assert results == _SAMPLE_RESULTS
    assert "query=ubuntu" in captured["url"]
    assert "page_size=10" in captured["url"]


def test_search_images_empty_query_no_request(monkeypatch):
    called = []

    def fake_open(req, *a, **k):
        called.append(req)
        raise AssertionError("no request expected")

    _patch_opener(monkeypatch, fake_open)
    assert search_mod.search_images("   ") == []
    assert search_mod.search_images(None) == []
    assert called == []


def test_search_images_retries_transient_then_succeeds(monkeypatch):
    calls = []

    def fake_open(req, *a, **k):
        calls.append(req.full_url)
        if len(calls) < 3:
            raise __import__("urllib.error").error.URLError("connection reset")
        return _FakeResp({"results": _SAMPLE_RESULTS})

    _patch_opener(monkeypatch, fake_open)
    results = search_mod.search_images("ubuntu")
    assert len(calls) == 3
    assert results == _SAMPLE_RESULTS


def test_search_images_missing_results_key(monkeypatch):
    def fake_open(req, *a, **k):
        return _FakeResp({})

    _patch_opener(monkeypatch, fake_open)
    assert search_mod.search_images("nope") == []


def test_search_images_paginates_beyond_docker_hub_page_cap(monkeypatch):
    captured = []

    def fake_open(req, *a, **k):
        qs = urllib.parse.parse_qs(
            urllib.parse.urlparse(req.full_url).query
        )
        page = int(qs["page"][0])
        page_size = int(qs["page_size"][0])
        captured.append((page, page_size))
        return _FakeResp({"results": [
            {"name": f"lib/img-{page}-{i}", "description": ""}
            for i in range(page_size)
        ]})

    _patch_opener(monkeypatch, fake_open)
    results = search_mod.search_images("ubuntu", limit=150)
    assert len(results) == 150
    assert captured == [(1, 100), (2, 50)]


def test_search_images_unlimited_walks_until_api_stops(monkeypatch):
    """The default (no limit) fetches every repository the API will
    serve; a 403 on the page past the last one ends the walk with what
    we have instead of failing the search."""
    captured = []

    def fake_open(req, *a, **k):
        qs = urllib.parse.parse_qs(
            urllib.parse.urlparse(req.full_url).query
        )
        page = int(qs["page"][0])
        page_size = int(qs["page_size"][0])
        captured.append((page, page_size))
        if page > 2:
            raise __import__("urllib.error").error.HTTPError(
                req.full_url, 403, "Forbidden", {}, None)
        return _FakeResp({"results": [
            {"name": f"lib/img-{page}-{i}", "description": ""}
            for i in range(page_size)
        ]})

    _patch_opener(monkeypatch, fake_open)
    results = search_mod.search_images("ubuntu")
    assert len(results) == 200
    assert captured == [(1, 100), (2, 100), (3, 100)]


def test_search_images_unlimited_stops_on_short_page(monkeypatch):
    """With no limit, a short page — not just an HTTP error — ends the
    walk; every result the API has given so far is kept."""
    captured = []

    def fake_open(req, *a, **k):
        qs = urllib.parse.parse_qs(
            urllib.parse.urlparse(req.full_url).query
        )
        page = int(qs["page"][0])
        page_size = int(qs["page_size"][0])
        captured.append(page)
        n = page_size if page == 1 else 50
        return _FakeResp({"results": [
            {"name": f"lib/img-{page}-{i}", "description": ""}
            for i in range(n)
        ]})

    _patch_opener(monkeypatch, fake_open)
    results = search_mod.search_images("ubuntu")
    assert len(results) == 150
    assert captured == [1, 2]


def test_search_images_first_page_error_propagates(monkeypatch):
    """A failure on the first page is a real network problem and must
    reach the caller, not be swallowed as 'end of results'."""
    def boom(req, *a, **k):
        raise __import__("urllib.error").error.URLError("offline")

    _patch_opener(monkeypatch, boom)
    with pytest.raises(__import__("urllib.error").error.URLError):
        search_mod.search_images("ubuntu")


def test_search_images_stops_when_a_page_comes_back_short(monkeypatch):
    captured = []

    def fake_open(req, *a, **k):
        qs = urllib.parse.parse_qs(
            urllib.parse.urlparse(req.full_url).query
        )
        page = int(qs["page"][0])
        page_size = int(qs["page_size"][0])
        captured.append(page)
        n = page_size if page == 1 else page_size // 2
        return _FakeResp({"results": [
            {"name": f"lib/img-{page}-{i}", "description": ""}
            for i in range(n)
        ]})

    _patch_opener(monkeypatch, fake_open)
    results = search_mod.search_images("ubuntu", limit=250)
    assert len(results) == 150
    assert captured == [1, 2]


# ----- image_architectures ------------------------------------------------

class _FakeRawResp:
    """A response whose body is *raw* bytes — for non-JSON payloads."""

    def __init__(self, raw):
        self._buf = io.BytesIO(raw)

    def read(self, *a):
        return self._buf.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_tag_api(monkeypatch, fn):
    monkeypatch.setattr(search_mod, "opener",
                        lambda insecure=False: _FakeOpener(fn))


def _tag_images(*archs):
    """Turn ("amd64", "linux", None) tuples into a tag-API images list."""
    return [
        {"architecture": a, "os": o, "variant": v} for a, o, v in archs
    ]


def test_image_architectures_from_tag_api(monkeypatch):
    payload = {"name": "latest", "images": _tag_images(
        ("amd64", "linux", None),
        ("arm", "linux", "v7"),
        ("arm64", "linux", "v8"),
        ("ppc64le", "linux", None),
        ("amd64", "windows", None),     # not linux: skipped
        ("unknown", "unknown", None),   # Docker Hub placeholder: skipped
        ("386", "linux", None),
    )}
    _patch_tag_api(monkeypatch, lambda req, *a, **k: _FakeResp(payload))
    assert search_mod.image_architectures("library/ubuntu") == \
        ["aarch64", "arm", "i686", "ppc64le", "x86_64"]


def test_image_architectures_single_architecture(monkeypatch):
    payload = {"name": "latest", "images": _tag_images(
        ("amd64", "linux", None),
    )}
    _patch_tag_api(monkeypatch, lambda req, *a, **k: _FakeResp(payload))
    assert search_mod.image_architectures("library/ubuntu-old") == ["x86_64"]


def test_image_architectures_missing_latest_tag(monkeypatch):
    def http404(req, *a, **k):
        raise __import__("urllib.error").error.HTTPError(
            req.full_url, 404, "Not Found", {}, None)

    _patch_tag_api(monkeypatch, http404)
    assert search_mod.image_architectures("myuser/no-latest") == []


def test_image_architectures_network_error_is_empty(monkeypatch):
    def boom(req, *a, **k):
        raise __import__("urllib.error").error.URLError("offline")

    _patch_tag_api(monkeypatch, boom)
    assert search_mod.image_architectures("myuser/secret") == []


def test_image_architectures_malformed_body(monkeypatch):
    _patch_tag_api(monkeypatch,
                   lambda req, *a, **k: _FakeRawResp(b"not json at all"))
    assert search_mod.image_architectures("library/broken") == []


def test_image_architectures_no_images_key(monkeypatch):
    _patch_tag_api(monkeypatch, lambda req, *a, **k: _FakeResp({}))
    assert search_mod.image_architectures("library/nope") == []


def test_image_architectures_url_is_the_latest_tag_endpoint(monkeypatch):
    captured = []

    def fake_open(req, *a, **k):
        captured.append(req.full_url)
        return _FakeResp({"images": _tag_images(("amd64", "linux", None))})

    _patch_tag_api(monkeypatch, fake_open)
    search_mod.image_architectures("myuser/img")
    assert captured == [
        "https://hub.docker.com/v2/repositories/myuser/img/tags/latest"
    ]


def test_is_installable():
    assert search_mod.is_installable(["x86_64", "aarch64"])
    assert search_mod.is_installable(["ppc64le", "arm"])
    assert not search_mod.is_installable([])
    assert not search_mod.is_installable(["ppc64le", "s390x"])


# ----- command ------------------------------------------------------------

def _cmd_args(**over):
    base = dict(query="ubuntu", limit=200, quiet=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_command_search_quiet_prints_names(monkeypatch, capsys):
    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=200: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures",
                        lambda ref, insecure=False: ["aarch64", "x86_64"])
    search_cmd.command_search(_cmd_args(quiet=True))
    out = capsys.readouterr().out
    assert out.splitlines() == ["library/ubuntu", "myuser/ubuntu-lite"]


def test_command_search_table(monkeypatch, capsys):
    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=200: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures",
                        lambda ref, insecure=False: ["aarch64", "x86_64"])
    monkeypatch.setattr(search_cmd, "terminal_width", lambda default=80: 120)
    search_cmd.command_search(_cmd_args())
    err = capsys.readouterr().err
    assert "NAME" in err and "DESCRIPTION" in err and "STARS" in err
    assert "ARCH" in err
    assert "[OK]" in err          # official / automated flags rendered
    assert "library/ubuntu" in err
    assert "myuser/ubuntu-lite" in err
    assert "aarch64/x86_64" in err
    assert "Resolving architectures" not in err


def test_command_search_resolves_architectures(monkeypatch, capsys):
    captured = []

    def fake_arch(ref, insecure=False):
        captured.append(ref)
        return {"library/ubuntu": ["x86_64", "arm64"],
                "myuser/ubuntu-lite": ["arm"]}[ref]

    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=200: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures", fake_arch)
    monkeypatch.setattr(search_cmd, "terminal_width", lambda default=80: 120)
    search_cmd.command_search(_cmd_args())
    assert captured == ["library/ubuntu", "myuser/ubuntu-lite"]
    err = capsys.readouterr().err
    assert "x86_64/arm64" in err and "arm" in err


def test_command_search_quiet_resolves_and_filters(monkeypatch, capsys):
    """--quiet must resolve architectures too, so the names piped into
    `install` are only installable images."""
    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=200: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures",
                        lambda ref, insecure=False: {
                            "library/ubuntu": ["x86_64"],
                            "myuser/ubuntu-lite": [],
                        }[ref])
    search_cmd.command_search(_cmd_args(quiet=True))
    out = capsys.readouterr().out
    assert out.splitlines() == ["library/ubuntu"]


def test_command_search_drops_unknown_arch(monkeypatch, capsys):
    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=200: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures",
                        lambda ref, insecure=False: [])
    search_cmd.command_search(_cmd_args())
    err = capsys.readouterr().err
    assert "?" not in err
    assert "No installable images" in err


def test_command_search_drops_unsupported_arch_only(monkeypatch, capsys):
    """A hit shipping only architectures proot-distro cannot install is
    dropped; one with a single supported arch is kept."""
    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=200: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures",
                        lambda ref, insecure=False: {
                            "library/ubuntu": ["ppc64le", "x86_64"],
                            "myuser/ubuntu-lite": ["s390x"],
                        }[ref])
    monkeypatch.setattr(search_cmd, "terminal_width", lambda default=80: 120)
    search_cmd.command_search(_cmd_args())
    err = capsys.readouterr().err
    assert "library/ubuntu" in err
    assert "myuser/ubuntu-lite" not in err
    assert "ppc64le/x86_64" in err
    assert "s390x" not in err


def test_command_search_drop_note_logged(monkeypatch, capsys):
    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=200: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures",
                        lambda ref, insecure=False: {
                            "library/ubuntu": ["x86_64"],
                            "myuser/ubuntu-lite": [],
                        }[ref])
    search_cmd.command_search(_cmd_args(quiet=True))
    err = capsys.readouterr().err
    assert "Dropped 1 image(s)" in err


def test_command_search_empty_results(monkeypatch, capsys):
    monkeypatch.setattr(search_cmd, "search_images", lambda q, limit=200: [])
    search_cmd.command_search(_cmd_args(query="zzzznone"))
    err = capsys.readouterr().err
    assert "No installable images" in err


def test_command_search_missing_query(capsys):
    with pytest.raises(SystemExit) as exc:
        search_cmd.command_search(_cmd_args(query=None))
    assert exc.value.code == 1
    assert "search term" in capsys.readouterr().err


def test_command_search_network_error(monkeypatch, capsys):
    def boom(q, limit=200):
        raise __import__("urllib.error").error.URLError("offline")

    monkeypatch.setattr(search_cmd, "search_images", boom)
    with pytest.raises(SystemExit) as exc:
        search_cmd.command_search(_cmd_args())
    assert exc.value.code == 1
    assert "Network error" in capsys.readouterr().err


def test_command_search_limit_passed_through(monkeypatch):
    captured = {}

    def fake_search(q, limit=200):
        captured["q"] = q
        captured["limit"] = limit
        return []

    monkeypatch.setattr(search_cmd, "search_images", fake_search)
    search_cmd.command_search(_cmd_args(query="alpine", limit=7))
    assert captured == {"q": "alpine", "limit": 7}


def test_ellipsize_truncates():
    assert search_cmd._ellipsize("short", 20) == "short"
    assert search_cmd._ellipsize("a long description here", 12) == \
        "a long desc…"
    assert search_cmd._ellipsize("abc", 1) == "a"
