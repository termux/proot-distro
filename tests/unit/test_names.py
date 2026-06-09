# Tests for proot_distro.names — the container-name format gate used at
# every entry point that accepts a name.

import pytest

from proot_distro import names


@pytest.mark.parametrize("name", [
    "ubuntu",
    "Ubuntu24",
    "a",
    "0",
    "my.distro",
    "my-distro_1",
    "x" * 200,
    "1ubuntu",
    "a.b-c_d.e",
])
def test_valid_names(name):
    assert names.is_valid_name(name) is True


@pytest.mark.parametrize("name", [
    "",
    ".hidden",        # must start alnum
    "-leading",
    "_leading",
    "..",
    ".",
    "../etc",
    "a/b",            # slash not allowed
    "a b",            # space not allowed
    "a:b",            # colon not allowed
    "a\tb",
    "naïve",          # non-ASCII
    "a$b",
    "a;b",
    "foo\r",          # carriage return is rejected (only \n hits the $ gotcha)
    "foo\nbar",       # embedded newline (not just trailing)
])
def test_invalid_names(name):
    assert names.is_valid_name(name) is False


def test_trailing_newline_should_be_rejected():
    assert names.is_valid_name("foo\n") is False


def test_is_valid_name_none_safe():
    assert names.is_valid_name(None) is False


def test_require_valid_name_passes_silently():
    # Returns None and does not raise/exit for a valid name.
    assert names.require_valid_name("ubuntu") is None


def test_require_valid_name_exits_with_message(capsys):
    with pytest.raises(SystemExit) as exc:
        names.require_valid_name("../evil", kind="new container name")
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "new container name" in err
    assert "../evil" in err
    # The shared rule hint is included.
    assert "begin with a letter or digit" in err
