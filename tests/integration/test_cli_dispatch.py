# Integration tests for cli.main — argument routing, alias resolution,
# unknown-command/required-arg errors, help, quiet, and the `--` separator.

import sys

import pytest

from proot_distro import cli, message
from proot_distro.arch import get_device_cpu_arch


def _run(monkeypatch, argv):
    """Invoke cli.main() with a fake argv; return the exit code (or None)."""
    monkeypatch.setattr(sys, "argv", ["proot-distro"] + argv)
    # Make the proot probe host-independent.
    monkeypatch.setattr(cli.shutil, "which", lambda _name: "/usr/bin/proot")
    try:
        cli.main()
        return None
    except SystemExit as exc:
        return exc.code


def test_no_args_shows_help(monkeypatch, capsys):
    assert _run(monkeypatch, []) == 0
    # Help text mentions usage/commands.
    assert capsys.readouterr().err  # something was printed


def test_unknown_command(monkeypatch, capsys):
    assert _run(monkeypatch, ["frobnicate"]) == 1
    assert "unknown command" in capsys.readouterr().err


def test_alias_resolves_and_requires_arg(monkeypatch, capsys):
    # `i` is an alias for install; with no image it must report the missing arg.
    assert _run(monkeypatch, ["i"]) == 1
    assert "not specified" in capsys.readouterr().err


def test_per_command_help(monkeypatch, capsys):
    assert _run(monkeypatch, ["install", "-h"]) == 0
    assert capsys.readouterr().err  # help rendered


def test_list_runs(monkeypatch, capsys):
    # `list` returns normally (no SystemExit) with no containers installed.
    assert _run(monkeypatch, ["list"]) is None
    assert "No containers" in capsys.readouterr().err


def test_quiet_flag_sets_global(monkeypatch):
    # clear-cache accepts -q and is harmless; the global quiet flag must be set.
    _run(monkeypatch, ["clear-cache", "-q"])
    assert message.is_quiet() is True


def test_search_missing_query(monkeypatch, capsys):
    assert _run(monkeypatch, ["search"]) == 1
    assert "search query is not specified" in capsys.readouterr().err


def test_search_dispatches_without_proot(monkeypatch, capsys):
    # Searching must not require proot on the host: nothing is run, the
    # command only queries Docker Hub.
    monkeypatch.setattr(cli.shutil, "which", lambda _name: None)
    seen = {}

    def fake_search(query, limit):
        seen["query"] = query
        seen["limit"] = limit
        return [], 0

    monkeypatch.setattr(
        "proot_distro.commands.search.search_images", fake_search
    )
    assert _run(monkeypatch, ["s", "ubuntu", "--limit", "3"]) is None
    assert seen == {"query": "ubuntu", "limit": 3}


def test_search_quiet_sets_global_quiet(monkeypatch, capsys):
    monkeypatch.setattr(
        "proot_distro.commands.search.search_images",
        lambda _q, _l: ([{"name": "ubuntu", "description": "", "stars": 1,
                          "pulls": 1, "official": False,
                          "automated": False}], 1),
    )
    _run(monkeypatch, ["search", "-q", "ubuntu"])
    assert message.is_quiet() is True
    assert capsys.readouterr().out == "ubuntu\n"


def test_unknown_option_rejected(monkeypatch, capsys):
    assert _run(monkeypatch, ["list", "--bogus-option"]) == 1
    assert "unrecognized option" in capsys.readouterr().err


def test_login_separator_inner_command(monkeypatch, capsys, builders):
    # Tokens after `--` become the inner command; --get-proot-cmd prints+exits.
    builders.make_container("box", arch=get_device_cpu_arch())
    code = _run(monkeypatch,
                ["login", "box", "--get-proot-cmd", "--", "echo", "hi"])
    assert code == 0
    out = capsys.readouterr().out
    assert "echo" in out and "hi" in out
