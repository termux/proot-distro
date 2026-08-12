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
    results = search_mod.search_images("ubuntu", limit=250)
    assert len(results) == 250
    assert captured == [(1, 100), (2, 100), (3, 50)]


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

class _FakeManifestResp:
    def __init__(self, payload, content_type):
        self._buf = io.BytesIO(json.dumps(payload).encode())
        self.headers = {"Content-Type": content_type}

    def read(self, *a):
        return self._buf.read(*a)

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _patch_manifest_io(monkeypatch, resp):
    monkeypatch.setattr(
        search_mod, "get_auth_token",
        lambda repo, registry="", insecure=False: (
            "tok", "https://registry-1.docker.io"
        ),
    )
    monkeypatch.setattr(search_mod, "opener",
                        lambda insecure=False: _FakeOpener(
                            lambda req, *a, **k: resp
                        ))


def test_image_architectures_from_index(monkeypatch):
    index = {
        "mediaType":
            "application/vnd.docker.distribution.manifest.list.v2+json",
        "manifests": [
            {"platform": {"os": "linux", "architecture": "amd64"}},
            {"platform": {"os": "linux", "architecture": "arm64"}},
            {"platform": {"os": "linux", "architecture": "arm",
                          "variant": "v7"}},
            {"platform": {"os": "linux", "architecture": "ppc64le"}},
            {"platform": {"os": "windows", "architecture": "amd64"}},
            {"platform": {"os": "linux"}},
        ],
    }
    _patch_manifest_io(monkeypatch, _FakeManifestResp(
        index, "application/vnd.docker.distribution.manifest.list.v2+json"))
    assert search_mod.image_architectures("library/ubuntu") == \
        ["aarch64", "arm", "ppc64le", "x86_64"]


def test_image_architectures_from_content_type_header(monkeypatch):
    index = {"manifests": [
        {"platform": {"os": "linux", "architecture": "amd64"}},
    ]}
    _patch_manifest_io(monkeypatch, _FakeManifestResp(
        index, "application/vnd.oci.image.index.v1+json; charset=utf-8"))
    assert search_mod.image_architectures("library/busybox") == ["x86_64"]


def test_image_architectures_single_manifest_is_empty(monkeypatch):
    single = {"mediaType":
              "application/vnd.docker.distribution.manifest.v2+json",
              "layers": [{"digest": "sha256:abc"}]}
    _patch_manifest_io(monkeypatch, _FakeManifestResp(
        single, "application/vnd.docker.distribution.manifest.v2+json"))
    assert search_mod.image_architectures("library/single") == []


def test_image_architectures_network_error_is_empty(monkeypatch):
    def boom(repo, registry="", insecure=False):
        raise __import__("urllib.error").error.URLError("offline")

    monkeypatch.setattr(search_mod, "get_auth_token", boom)
    assert search_mod.image_architectures("myuser/secret") == []


def test_image_architectures_manifest_error_is_empty(monkeypatch):
    _patch_manifest_io(monkeypatch, _FakeManifestResp(
        {}, "application/json"))
    assert search_mod.image_architectures("library/nope") == []


# ----- command ------------------------------------------------------------

def _cmd_args(**over):
    base = dict(query="ubuntu", limit=100, quiet=False)
    base.update(over)
    return SimpleNamespace(**base)


def test_command_search_quiet_prints_names(monkeypatch, capsys):
    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=100: _SAMPLE_RESULTS)
    search_cmd.command_search(_cmd_args(quiet=True))
    out = capsys.readouterr().out
    assert out.splitlines() == ["library/ubuntu", "myuser/ubuntu-lite"]


def test_command_search_table(monkeypatch, capsys):
    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=100: _SAMPLE_RESULTS)
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
    assert "Resolving architectures" in err


def test_command_search_resolves_architectures(monkeypatch, capsys):
    captured = []

    def fake_arch(ref, insecure=False):
        captured.append(ref)
        return {"library/ubuntu": ["x86_64", "arm64"],
                "myuser/ubuntu-lite": ["arm"]}[ref]

    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=100: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures", fake_arch)
    monkeypatch.setattr(search_cmd, "terminal_width", lambda default=80: 120)
    search_cmd.command_search(_cmd_args())
    assert captured == ["library/ubuntu", "myuser/ubuntu-lite"]
    err = capsys.readouterr().err
    assert "x86_64/arm64" in err and "arm" in err


def test_command_search_quiet_skips_architectures(monkeypatch, capsys):
    def boom(ref, insecure=False):
        raise AssertionError("quiet search must not fetch architectures")

    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=100: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures", boom)
    search_cmd.command_search(_cmd_args(quiet=True))
    out = capsys.readouterr().out
    assert out.splitlines() == ["library/ubuntu", "myuser/ubuntu-lite"]


def test_command_search_unknown_arch_question_mark(monkeypatch, capsys):
    monkeypatch.setattr(search_cmd, "search_images",
                        lambda q, limit=100: _SAMPLE_RESULTS)
    monkeypatch.setattr(search_cmd, "image_architectures",
                        lambda ref, insecure=False: [])
    search_cmd.command_search(_cmd_args())
    err = capsys.readouterr().err
    assert "?" in err


def test_command_search_empty_results(monkeypatch, capsys):
    monkeypatch.setattr(search_cmd, "search_images", lambda q, limit=100: [])
    search_cmd.command_search(_cmd_args(query="zzzznone"))
    err = capsys.readouterr().err
    assert "No results" in err


def test_command_search_missing_query(capsys):
    with pytest.raises(SystemExit) as exc:
        search_cmd.command_search(_cmd_args(query=None))
    assert exc.value.code == 1
    assert "search term" in capsys.readouterr().err


def test_command_search_network_error(monkeypatch, capsys):
    def boom(q, limit=100):
        raise __import__("urllib.error").error.URLError("offline")

    monkeypatch.setattr(search_cmd, "search_images", boom)
    with pytest.raises(SystemExit) as exc:
        search_cmd.command_search(_cmd_args())
    assert exc.value.code == 1
    assert "Network error" in capsys.readouterr().err


def test_command_search_limit_passed_through(monkeypatch):
    captured = {}

    def fake_search(q, limit=100):
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
