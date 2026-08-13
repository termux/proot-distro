# Tests for the `search` command: Docker Hub's search endpoint
# (helpers/docker/search.py — response sanitising, paging, error
# translation) and its presentation (commands/search.py — limit
# validation, count formatting, table vs stacked layout, --quiet).
# No network: _fetch_page / opener are stubbed throughout.

import io
import json
import urllib.error

import pytest

from proot_distro.commands import search as cmd
from proot_distro.helpers import download
from proot_distro.helpers.docker import search as hub


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    # search shares download.retry_http; keep transient-failure paths fast.
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)


def _hit(name, **over):
    entry = {
        "name": name,
        "description": f"description of {name}",
        "star_count": 3,
        "pull_count": 4000,
        "is_official": False,
        "is_automated": False,
    }
    entry.update(over)
    return entry


def _page(names, num_results=None, num_pages=1):
    return {
        "results": [_hit(n) for n in names],
        "num_results": len(names) if num_results is None else num_results,
        "num_pages": num_pages,
    }


# ----- response sanitising -------------------------------------------------

@pytest.mark.parametrize("name", [
    "Ubuntu",                      # uppercase is not a legal repo name
    "has space",
    "trailing/",
    "/leading",
    "esc\x1b[31mape",
    "a" * 256,                     # over the length ceiling
    "",
    None,
    12345,
])
def test_normalize_drops_unusable_names(name):
    assert hub._normalize(_hit(name)) is None


@pytest.mark.parametrize("name", [
    "ubuntu", "library/ubuntu", "my-user/my_image", "a.b/c__d", "x---y/z",
])
def test_normalize_accepts_legal_names(name):
    assert hub._normalize(_hit(name))["name"] == name


def test_normalize_drops_non_dict_entry():
    assert hub._normalize("nginx") is None
    assert hub._normalize(None) is None


def test_normalize_collapses_and_escapes_description():
    hit = hub._normalize(_hit(
        "evil", description="first\nsecond   third\ttab\x1b[2Jclear",
    ))
    # One line, no raw control characters, ESC rendered as a literal.
    assert "\n" not in hit["description"]
    assert "\x1b" not in hit["description"]
    assert hit["description"] == "first second third tab\\e[2Jclear"


def test_normalize_missing_description_becomes_empty():
    entry = _hit("nginx")
    del entry["description"]
    assert hub._normalize(entry)["description"] == ""
    assert hub._normalize(_hit("nginx", description=None))["description"] == ""


@pytest.mark.parametrize("value,expected", [
    (7, 7),
    ("42", 42),
    (12.9, 12),
    (-5, 0),          # a negative count is meaningless
    (True, 0),        # a bool is not a count, though int() accepts it
    (None, 0),
    ("many", 0),
    ([1], 0),
])
def test_count_sanitising(value, expected):
    assert hub._count(value) == expected


def test_normalize_carries_flags_and_counts():
    hit = hub._normalize(_hit(
        "nginx", star_count=21357, pull_count=13253167718, is_official=True,
    ))
    assert hit["stars"] == 21357
    assert hit["pulls"] == 13253167718
    assert hit["official"] is True
    assert hit["automated"] is False


# ----- search_images: paging and limits ------------------------------------

def _stub_pages(monkeypatch, pages):
    """Serve *pages* in order, recording each (query, page, n) request."""
    calls = []

    def fake_fetch(query, page, page_size):
        calls.append((query, page, page_size))
        return pages[page - 1]

    monkeypatch.setattr(hub, "_fetch_page", fake_fetch)
    return calls


def test_single_page_request(monkeypatch):
    calls = _stub_pages(monkeypatch, [_page(["nginx", "ubuntu"], 500)])
    results, total = hub.search_images("nginx", limit=25)
    assert [r["name"] for r in results] == ["nginx", "ubuntu"]
    assert total == 500
    assert calls == [("nginx", 1, 25)]


def test_limit_smaller_than_page(monkeypatch):
    _stub_pages(monkeypatch, [_page(["a", "b", "c"], 90)])
    results, _ = hub.search_images("x", limit=2)
    assert [r["name"] for r in results] == ["a", "b"]


def test_pagination_keeps_page_size_constant(monkeypatch):
    # 250 wanted, Hub serves 100 per request: three full-size pages, then
    # the surplus is trimmed. Shrinking `n` on the last page would shift
    # the server-side offset and re-fetch rows already held.
    pages = [
        _page([f"p1-{i}" for i in range(100)], 9999, num_pages=99),
        _page([f"p2-{i}" for i in range(100)], 9999, num_pages=99),
        _page([f"p3-{i}" for i in range(100)], 9999, num_pages=99),
    ]
    calls = _stub_pages(monkeypatch, pages)
    results, total = hub.search_images("x", limit=250)
    assert len(results) == 250
    assert [n for _q, _p, n in calls] == [100, 100, 100]
    assert [p for _q, p, _n in calls] == [1, 2, 3]
    assert results[-1]["name"] == "p3-49"
    assert total == 9999


def test_short_page_ends_the_walk(monkeypatch):
    pages = [_page([f"p1-{i}" for i in range(100)], 130, num_pages=2),
             _page([f"p2-{i}" for i in range(30)], 130, num_pages=2)]
    calls = _stub_pages(monkeypatch, pages)
    results, _ = hub.search_images("x", limit=300)
    assert len(results) == 130
    assert len(calls) == 2


def test_num_pages_ends_the_walk(monkeypatch):
    # A full final page, but Hub says there is only one.
    pages = [_page([f"p1-{i}" for i in range(100)], 100, num_pages=1)]
    calls = _stub_pages(monkeypatch, pages)
    results, _ = hub.search_images("x", limit=300)
    assert len(results) == 100
    assert len(calls) == 1


def test_duplicate_names_are_dropped(monkeypatch):
    _stub_pages(monkeypatch, [_page(["a", "a", "b"], 3)])
    results, _ = hub.search_images("x", limit=25)
    assert [r["name"] for r in results] == ["a", "b"]


def test_junk_entries_do_not_consume_the_limit(monkeypatch):
    page = {"results": [_hit("BAD NAME"), _hit("good"), "nonsense"],
            "num_results": 3, "num_pages": 1}
    _stub_pages(monkeypatch, [page])
    results, _ = hub.search_images("x", limit=25)
    assert [r["name"] for r in results] == ["good"]


def test_limit_is_clamped_to_ceiling(monkeypatch):
    calls = _stub_pages(monkeypatch, [_page(["a"], 1)])
    hub.search_images("x", limit=10 ** 9)
    # Clamped to SEARCH_LIMIT_MAX, so the page size is Hub's own maximum.
    assert calls[0][2] == hub.SEARCH_PAGE_MAX


def test_limit_below_one_still_asks_for_one_page(monkeypatch):
    calls = _stub_pages(monkeypatch, [_page(["a"], 1)])
    results, _ = hub.search_images("x", limit=0)
    assert len(results) == 1
    assert calls == [("x", 1, 1)]


def test_total_is_never_less_than_the_rows_returned(monkeypatch):
    _stub_pages(monkeypatch, [_page(["a", "b"], num_results=0)])
    _results, total = hub.search_images("x", limit=25)
    assert total == 2


@pytest.mark.parametrize("query", ["", "   ", "\t"])
def test_empty_query_refused(query):
    with pytest.raises(RuntimeError):
        hub.search_images(query)


def test_malformed_results_field(monkeypatch):
    monkeypatch.setattr(
        hub, "_fetch_page", lambda *a: {"results": "not a list"}
    )
    with pytest.raises(RuntimeError, match="malformed"):
        hub.search_images("x")


# ----- _fetch_page: transport and error translation ------------------------

class _FakeOpener:
    def __init__(self, payload=None, exc=None):
        self.payload = payload
        self.exc = exc
        self.requests = []

    def open(self, req, timeout=None):
        self.requests.append((req, timeout))
        if self.exc is not None:
            raise self.exc
        body = json.dumps(self.payload).encode()

        class _Resp(io.BytesIO):
            def __enter__(self_inner):
                return self_inner

            def __exit__(self_inner, *_exc):
                return False

        return _Resp(body)


def _install_opener(monkeypatch, fake):
    monkeypatch.setattr(hub, "opener", lambda *a, **k: fake)
    return fake


def test_fetch_page_builds_the_query(monkeypatch):
    fake = _install_opener(monkeypatch, _FakeOpener(payload=_page(["a"])))
    data = hub._fetch_page("my query", 2, 100)
    assert data["results"][0]["name"] == "a"
    url = fake.requests[0][0].full_url
    assert url.startswith(hub.SEARCH_URL + "?")
    assert "q=my+query" in url
    assert "n=100" in url
    assert "page=2" in url
    # A stalled connection must not hang the command forever.
    assert fake.requests[0][1] == hub._TIMEOUT


def test_fetch_page_sends_no_credentials(monkeypatch):
    monkeypatch.setenv("PD_DOCKER_AUTH", "user:password")
    fake = _install_opener(monkeypatch, _FakeOpener(payload=_page(["a"])))
    hub._fetch_page("x", 1, 25)
    assert fake.requests[0][0].get_header("Authorization") is None


def test_fetch_page_http_error_becomes_runtime_error(monkeypatch):
    err = urllib.error.HTTPError(hub.SEARCH_URL, 404, "Not Found", {}, None)
    _install_opener(monkeypatch, _FakeOpener(exc=err))
    with pytest.raises(RuntimeError, match="HTTP 404"):
        hub._fetch_page("x", 1, 25)


def test_fetch_page_certificate_error_points_at_the_host(monkeypatch):
    import ssl
    err = urllib.error.URLError(ssl.SSLCertVerificationError("bad cert"))
    _install_opener(monkeypatch, _FakeOpener(exc=err))
    with pytest.raises(RuntimeError, match="index.docker.io"):
        hub._fetch_page("x", 1, 25)


def test_fetch_page_connection_error_propagates(monkeypatch):
    # Not translated: the command reports it as a network error.
    err = urllib.error.URLError(OSError("no route to host"))
    _install_opener(monkeypatch, _FakeOpener(exc=err))
    with pytest.raises(urllib.error.URLError):
        hub._fetch_page("x", 1, 25)


def test_fetch_page_malformed_json(monkeypatch):
    class _BadOpener(_FakeOpener):
        def open(self, req, timeout=None):
            class _Resp(io.BytesIO):
                def __enter__(self_inner):
                    return self_inner

                def __exit__(self_inner, *_exc):
                    return False

            return _Resp(b"<html>not json</html>")

    _install_opener(monkeypatch, _BadOpener())
    with pytest.raises(RuntimeError, match="malformed"):
        hub._fetch_page("x", 1, 25)


def test_fetch_page_non_object_json(monkeypatch):
    _install_opener(monkeypatch, _FakeOpener(payload=["a", "b"]))
    with pytest.raises(RuntimeError, match="unexpected"):
        hub._fetch_page("x", 1, 25)


# ----- presentation --------------------------------------------------------

@pytest.mark.parametrize("value,expected", [
    (0, "0"),
    (999, "999"),
    (1000, "1.0K"),
    (15500, "15.5K"),
    (89859997, "89.9M"),
    (1086609459, "1.1B"),
    (13253167718, "13.3B"),
])
def test_fmt_count(value, expected):
    assert cmd._fmt_count(value) == expected


def test_fit_truncates_with_ellipsis():
    assert cmd._fit("short", 10) == "short"
    assert cmd._fit("exactly-10", 10) == "exactly-10"
    assert cmd._fit("abcdefghijk", 10) == "abcdefghi…"


def test_parse_limit_default_and_values():
    assert cmd._parse_limit(None) == hub.SEARCH_DEFAULT_LIMIT
    assert cmd._parse_limit("50") == 50
    assert cmd._parse_limit(" 7 ") == 7


@pytest.mark.parametrize("raw", ["abc", "1.5", "", "5x"])
def test_parse_limit_rejects_non_numbers(raw, capsys):
    with pytest.raises(SystemExit) as exc:
        cmd._parse_limit(raw)
    assert exc.value.code == 1
    assert "whole number" in capsys.readouterr().err


@pytest.mark.parametrize("raw", ["0", "-3"])
def test_parse_limit_rejects_below_one(raw, capsys):
    with pytest.raises(SystemExit):
        cmd._parse_limit(raw)
    assert "at least 1" in capsys.readouterr().err


def test_parse_limit_rejects_above_ceiling(capsys):
    with pytest.raises(SystemExit):
        cmd._parse_limit(str(hub.SEARCH_LIMIT_MAX + 1))
    assert str(hub.SEARCH_LIMIT_MAX) in capsys.readouterr().err


# ----- command --------------------------------------------------------------

class _Args:
    def __init__(self, query="nginx", limit=None, quiet=False):
        self.query = query
        self.limit = limit
        self.quiet = quiet


def _stub_results(monkeypatch, results, total=None, recorder=None):
    def fake_search(query, limit):
        if recorder is not None:
            recorder.append((query, limit))
        return results, len(results) if total is None else total

    monkeypatch.setattr(cmd, "search_images", fake_search)


_ROWS = [
    {"name": "nginx", "description": "Official build of Nginx.",
     "stars": 21357, "pulls": 13253167718, "official": True,
     "automated": False},
    {"name": "ubuntu/nginx", "description": "Nginx packaged by Ubuntu",
     "stars": 130, "pulls": 12400000, "official": False,
     "automated": False},
]


def test_quiet_prints_bare_names_to_stdout(monkeypatch, capsys):
    _stub_results(monkeypatch, _ROWS)
    cmd.command_search(_Args(quiet=True))
    out = capsys.readouterr()
    assert out.out == "nginx\nubuntu/nginx\n"
    assert out.err == ""


def test_table_layout_on_a_wide_terminal(monkeypatch, capsys):
    monkeypatch.setattr(cmd, "terminal_width", lambda *a, **k: 100)
    _stub_results(monkeypatch, _ROWS, total=291103)
    cmd.command_search(_Args())
    err = capsys.readouterr().err
    assert "NAME" in err and "DESCRIPTION" in err and "OFFICIAL" in err
    assert "nginx" in err and "13.3B" in err and "[OK]" in err
    assert "Showing 2 of 291103 matches." in err
    # A row without an OFFICIAL cell must not end in padding.
    row = [ln for ln in err.splitlines() if ln.startswith("ubuntu/nginx")][0]
    assert row == row.rstrip()


def test_stacked_layout_on_a_narrow_terminal(monkeypatch, capsys):
    monkeypatch.setattr(cmd, "terminal_width", lambda *a, **k: 40)
    _stub_results(monkeypatch, _ROWS)
    err = capsys.readouterr()  # drain
    cmd.command_search(_Args())
    err = capsys.readouterr().err
    assert "NAME" not in err  # no header row in the stacked form
    assert "* nginx [OK]" in err
    assert "21357 stars, 13.3B pulls" in err


def test_singular_star_and_pull_counts(monkeypatch, capsys):
    monkeypatch.setattr(cmd, "terminal_width", lambda *a, **k: 40)
    _stub_results(monkeypatch, [
        {"name": "solo", "description": "", "stars": 1, "pulls": 1,
         "official": False, "automated": False},
    ])
    cmd.command_search(_Args())
    assert "1 star, 1 pull" in capsys.readouterr().err


def test_no_matches_reports_and_exits_zero(monkeypatch, capsys):
    _stub_results(monkeypatch, [])
    cmd.command_search(_Args(query="zzzqqq"))
    out = capsys.readouterr()
    assert "No images found matching 'zzzqqq'" in out.err
    assert out.out == ""


def test_quiet_with_no_matches_prints_nothing(monkeypatch, capsys):
    _stub_results(monkeypatch, [])
    cmd.command_search(_Args(quiet=True))
    assert capsys.readouterr().out == ""


def test_limit_is_forwarded(monkeypatch):
    calls = []
    _stub_results(monkeypatch, _ROWS, recorder=calls)
    cmd.command_search(_Args(limit="50", quiet=True))
    assert calls == [("nginx", 50)]


def test_blank_query_is_refused(capsys):
    with pytest.raises(SystemExit) as exc:
        cmd.command_search(_Args(query="   "))
    assert exc.value.code == 1
    assert "query is empty" in capsys.readouterr().err


def test_query_is_escaped_in_messages(monkeypatch, capsys):
    _stub_results(monkeypatch, [])
    cmd.command_search(_Args(query="oops\x1b[2J"))
    err = capsys.readouterr().err
    assert "\x1b[2J" not in err
    assert "oops\\e[2J" in err


@pytest.mark.parametrize("exc,expected", [
    (urllib.error.URLError("down"), "Network error"),
    (OSError("broken pipe"), "Network error"),
    (RuntimeError("Docker Hub refused"), "Docker Hub refused"),
    (KeyboardInterrupt(), "Aborted by user"),
])
def test_failures_exit_one_with_a_message(monkeypatch, capsys, exc, expected):
    def boom(_query, _limit):
        raise exc

    monkeypatch.setattr(cmd, "search_images", boom)
    with pytest.raises(SystemExit) as info:
        cmd.command_search(_Args())
    assert info.value.code == 1
    assert expected in capsys.readouterr().err
