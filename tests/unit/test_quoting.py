# Tests for proot_distro.commands.login.quoting.dq — POSIX double-quoting
# used by `login --get-proot-cmd`. The key property is shell-injection safety.

import subprocess

import pytest

from proot_distro.commands.login.quoting import dq


@pytest.mark.parametrize("s", [
    "abc",
    "ABC123",
    "a/b.c-d_e:f@g=h",
    "+plus",
])
def test_safe_strings_returned_bare(s):
    assert dq(s) == s


def test_empty_string_is_quoted():
    assert dq("") == '""'


def test_spaces_are_quoted():
    assert dq("a b") == '"a b"'


def test_dollar_is_escaped():
    out = dq("$(rm -rf /)")
    assert out.startswith('"') and out.endswith('"')
    assert "\\$" in out


def test_double_quote_and_backslash_and_backtick_escaped():
    out = dq('a"b`c\\d')
    assert '\\"' in out
    assert "\\`" in out
    assert "\\\\" in out


@pytest.mark.parametrize("s", [
    "plain",
    "with space",
    "$(echo pwned)",
    "`echo pwned`",
    'embedded "quotes" here',
    "semicolon; rm -rf /",
    "back\\slash",
    "$HOME/path",
    "tab\tafter",
])
def test_roundtrips_through_real_shell(s):
    # Proves the quoting is injection-safe: a POSIX shell must reproduce the
    # exact input with no expansion/command-substitution.
    cmd = "printf %s " + dq(s)
    result = subprocess.run(
        ["sh", "-c", cmd], capture_output=True, text=True, check=True,
    )
    assert result.stdout == s
