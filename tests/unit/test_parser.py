# Tests for proot_distro.parser — argparse construction, alias table, and
# required-arg metadata.

import pytest

from proot_distro import parser
from proot_distro.constants import IS_TERMUX


def test_install_parsing():
    p = parser.build_parser()
    args, unknown = p.parse_known_args(["install", "ubuntu:24.04"])
    assert args.command == "install"
    assert args.image_ref == "ubuntu:24.04"
    assert unknown == []


def test_install_missing_positional_is_none():
    p = parser.build_parser()
    args, _ = p.parse_known_args(["install"])
    assert args.image_ref is None  # nargs="?", validated later by REQUIRED_ARGS


def test_custom_name_flag():
    p = parser.build_parser()
    args, _ = p.parse_known_args(["install", "ubuntu", "-n", "mybox"])
    assert args.custom_container_name == "mybox"


def test_install_allow_insecure_defaults_false():
    p = parser.build_parser()
    args, _ = p.parse_known_args(["install", "ubuntu"])
    assert args.allow_insecure is False


def test_install_allow_insecure_flag():
    p = parser.build_parser()
    args, unknown = p.parse_known_args(
        ["install", "192.168.1.1:5000/img:latest", "--allow-insecure"]
    )
    assert args.allow_insecure is True
    assert unknown == []


@pytest.mark.parametrize("alias,canonical", [
    ("add", "install"), ("i", "install"), ("in", "install"), ("ins", "install"),
    ("rm", "remove"),
    ("sh", "login"),
    ("li", "list"), ("ls", "list"),
    ("bak", "backup"), ("bkp", "backup"),
    ("clear", "clear-cache"), ("cl", "clear-cache"),
    ("cp", "copy"),
    ("s", "search"), ("se", "search"),
    ("h", "help"), ("he", "help"), ("hel", "help"),
])
def test_alias_table(alias, canonical):
    assert parser.ALIAS_TO_CANONICAL[alias] == canonical


def test_required_args_table():
    assert ("image_ref",) == tuple(a for a, _ in parser.REQUIRED_ARGS["install"])
    names = {a for a, _ in parser.REQUIRED_ARGS["rename"]}
    assert names == {"orig_name", "new_name"}
    # restore intentionally absent (decides from stdin TTY state).
    assert "restore" not in parser.REQUIRED_ARGS


def test_each_subcommand_parses():
    p = parser.build_parser()
    for argv, cmd in [
        (["remove", "box"], "remove"),
        (["rename", "a", "b"], "rename"),
        (["reset", "box"], "reset"),
        (["list"], "list"),
        (["ps"], "ps"),
        (["backup", "box"], "backup"),
        (["restore", "f.tar"], "restore"),
        (["clear-cache"], "clear-cache"),
        (["copy", "a", "b"], "copy"),
        (["sync", "a", "b"], "sync"),
        (["build", "."], "build"),
        (["push", "me/app:1"], "push"),
        (["run", "box"], "run"),
        (["login", "box"], "login"),
        (["kill", "box"], "kill"),
        (["search", "ubuntu"], "search"),
    ]:
        args, _ = p.parse_known_args(argv)
        assert args.command == cmd


@pytest.mark.parametrize("flag", ["-l", "--limit"])
def test_search_limit_flag(flag):
    p = parser.build_parser()
    args, unknown = p.parse_known_args(["search", "ubuntu", flag, "50"])
    assert args.command == "search"
    assert args.query == "ubuntu"
    # Kept as a string; commands/search.py validates and reports on it.
    assert args.limit == "50"
    assert unknown == []


def test_search_defaults():
    p = parser.build_parser()
    args, _ = p.parse_known_args(["se", "ubuntu"])
    assert args.limit is None
    assert args.quiet is False


def test_search_missing_query_is_none():
    p = parser.build_parser()
    args, _ = p.parse_known_args(["search"])
    assert args.query is None  # validated later by REQUIRED_ARGS
    assert ("query",) == tuple(a for a, _ in parser.REQUIRED_ARGS["search"])


def test_ps_quiet_flag():
    p = parser.build_parser()
    args, unknown = p.parse_known_args(["ps", "-q"])
    assert args.command == "ps"
    assert args.quiet is True
    assert unknown == []


@pytest.mark.parametrize("cmd", ["login", "run"])
def test_detach_flag_default_false(cmd):
    p = parser.build_parser()
    args, _ = p.parse_known_args([cmd, "box"])
    assert args.detach is False


@pytest.mark.parametrize("cmd,flag", [
    ("login", "-d"), ("login", "--detach"),
    ("run", "-d"), ("run", "--detach"),
])
def test_detach_flag_set(cmd, flag):
    p = parser.build_parser()
    args, unknown = p.parse_known_args([cmd, "box", flag])
    assert args.command == cmd
    assert args.detach is True
    assert unknown == []


def test_kill_pid_target():
    p = parser.build_parser()
    args, unknown = p.parse_known_args(["kill", "12345"])
    assert args.command == "kill"
    assert args.target == "12345"
    assert args.all is False
    assert args.signal is None
    assert unknown == []


def test_kill_container_target():
    p = parser.build_parser()
    args, _ = p.parse_known_args(["kill", "ubuntu"])
    assert args.target == "ubuntu"


def test_kill_all_flag():
    p = parser.build_parser()
    args, _ = p.parse_known_args(["kill", "--all"])
    assert args.target is None
    assert args.all is True


@pytest.mark.parametrize("flag", ["-s", "--signal"])
def test_kill_signal_flag(flag):
    p = parser.build_parser()
    args, _ = p.parse_known_args(["kill", flag, "TERM", "box"])
    assert args.signal == "TERM"
    assert args.target == "box"


def test_build_multiple_tags_and_outputs():
    p = parser.build_parser()
    args, _ = p.parse_known_args(
        ["build", ".", "-t", "a:1", "-t", "b:2", "-o", "x.tar"]
    )
    assert args.tags == ["a:1", "b:2"]
    assert args.outputs == ["x.tar"]


@pytest.mark.skipif(IS_TERMUX, reason="Termux-only flags present on Termux")
def test_termux_only_flags_absent_off_termux():
    p = parser.build_parser()
    _args, unknown = p.parse_known_args(["login", "box", "--isolated"])
    # --isolated is only registered on Termux, so off-Termux it is unknown.
    assert "--isolated" in unknown


def test_remove_positional_and_image_flag():
    p = parser.build_parser()
    args, unknown = p.parse_known_args(["remove", "box"])
    assert args.target == "box"
    assert args.image is False
    assert args.override_arch is None
    assert unknown == []

    args, unknown = p.parse_known_args(
        ["remove", "--image", "ubuntu:24.04", "-a", "aarch64"]
    )
    assert args.target == "ubuntu:24.04"
    assert args.image is True
    assert args.override_arch == "aarch64"
    assert unknown == []


@pytest.mark.parametrize("flag", ["-i", "--image"])
def test_remove_image_short_and_long_flag(flag):
    p = parser.build_parser()
    args, unknown = p.parse_known_args(["rm", flag, "ubuntu:24.04"])
    assert args.target == "ubuntu:24.04"
    assert args.image is True
    assert unknown == []


@pytest.mark.parametrize("flag", ["-i", "--image"])
def test_list_image_short_and_long_flag(flag):
    p = parser.build_parser()
    args, unknown = p.parse_known_args(["list", flag, "-q"])
    assert args.image is True
    assert args.quiet is True
    assert unknown == []


def test_list_image_defaults_off():
    p = parser.build_parser()
    args, _ = p.parse_known_args(["ls"])
    assert args.image is False


def test_required_args_for_switches_on_image_flag():
    from types import SimpleNamespace
    plain = parser.required_args_for("remove", SimpleNamespace(image=False))
    assert [a for a, _ in plain] == ["target"]
    assert "container name" in plain[0][1]

    imaged = parser.required_args_for("remove", SimpleNamespace(image=True))
    assert [a for a, _ in imaged] == ["target"]
    assert "image reference" in imaged[0][1]

    # Every other command falls through to the static table.
    assert parser.required_args_for("install", SimpleNamespace()) == \
        parser.REQUIRED_ARGS["install"]
