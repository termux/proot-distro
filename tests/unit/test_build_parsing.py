# Tests for proot_distro.helpers.build_engine.parsing — small stateless
# string/argv helpers used by the build handlers.

import os

import pytest

from proot_distro.helpers.build_engine import parsing


@pytest.mark.parametrize("value,expected", [
    ("K=V", ("K", "V")),
    ("K", ("K", None)),
    ("K=", ("K", "")),
    ("", ("", None)),
    ("K=a=b", ("K", "a=b")),
    # Outer whitespace stripped first, then key is .strip()'d; the value
    # keeps its leading inner space but the trailing one is already gone.
    ("  K = V ", ("K", " V")),
])
def test_split_arg(value, expected):
    assert parsing.split_arg(value) == expected


def test_split_arg_list_input():
    assert parsing.split_arg(["K=V"]) == ("K", "V")


@pytest.mark.parametrize("value,expected", [
    ("A=1 B=2", [("A", "1"), ("B", "2")]),
    ('A="x y" B=2', [("A", "x y"), ("B", "2")]),
    ("FOO bar baz", [("FOO", "bar baz")]),   # legacy ENV K V form
    ("FOO", [("FOO", "")]),
])
def test_parse_kv_list(value, expected):
    assert parsing.parse_kv_list(value) == expected


def test_to_argv_exec_form():
    instr = {"exec_form": True, "value": ["a", "b"]}
    assert parsing.to_argv(instr, ["/bin/sh", "-c"]) == ["a", "b"]


def test_to_argv_shell_form():
    instr = {"exec_form": False, "value": "echo hi"}
    assert parsing.to_argv(instr, ["/bin/sh", "-c"]) == [
        "/bin/sh", "-c", "echo hi"
    ]


@pytest.mark.parametrize("s,expected", [
    ("http://x/y", True),
    ("https://x/y", True),
    ("ftp://x", False),
    ("file:///x", False),
    ("plain", False),
])
def test_looks_like_url(s, expected):
    assert parsing.looks_like_url(s) is expected


def _head(path):
    """The bytes ADD's auto-extract sniffs, read the way it reads them."""
    with open(str(path), "rb") as fh:
        return fh.read(parsing.TAR_HEADER_BYTES)


def test_is_tar_header_ustar(tmp_path, builders):
    p = tmp_path / "a.tar"
    builders.make_tar(str(p), [{"name": "x", "type": "file", "data": b"hi"}])
    assert parsing.is_tar_header(_head(p)) is True


def test_is_tar_header_gzip(tmp_path, builders):
    p = tmp_path / "a.tar.gz"
    # Incompressible payload so the gzip header lands well past the 265-byte
    # minimum the signature probe requires.
    builders.make_tar(
        str(p),
        [{"name": "x", "type": "file", "data": os.urandom(4096)}],
        compression="gz",
    )
    assert parsing.is_tar_header(_head(p)) is True


def test_is_tar_header_rejects_non_tar(tmp_path):
    p = tmp_path / "not.tar"
    p.write_bytes(b"this is just text, definitely not a tar archive" * 10)
    assert parsing.is_tar_header(_head(p)) is False


def test_is_tar_header_short_file(tmp_path):
    # Too few bytes to hold the ustar magic at all — not an archive.
    p = tmp_path / "tiny"
    p.write_bytes(b"abc")
    assert parsing.is_tar_header(_head(p)) is False
    assert parsing.is_tar_header(b"") is False


# ----- COPY/ADD --chmod ---------------------------------------------------

def _instr(name="COPY", lineno=3):
    return {"name": name, "lineno": lineno}


@pytest.mark.parametrize("value,expected", [
    ("644", 0o644),
    ("0755", 0o755),
    ("0", 0),
    ("7777", 0o7777),
    ("07777", 0o7777),
    ("00000644", 0o644),
])
def test_parse_chmod_accepts_octal_up_to_07777(value, expected):
    assert parsing.parse_chmod(value, _instr()) == expected


@pytest.mark.parametrize("value", [
    "",              # `--chmod=` and a bare `--chmod`
    "u+x",           # symbolic
    "0x1ed",         # hex
    "755 ",          # stray whitespace
    "8",             # not octal
    "17777",         # more than a mode can carry (the kernel masked it)
    "77777777777777",  # more than a C int can hold (OverflowError)
    "-1",
])
def test_parse_chmod_refuses_anything_else(value):
    # Both halves of the old behaviour were wrong: a value that was not
    # all octal digits was quietly "no override", so the image was built
    # with the sources' own modes and nothing said so; and one that was
    # had no upper bound, so it reached os.fchmod() as an int C cannot
    # hold, an OverflowError neither of build's nets catches.
    from proot_distro.helpers.build_engine.errors import BuildError
    with pytest.raises(BuildError) as exc:
        parsing.parse_chmod(value, _instr("ADD", 7))
    assert "ADD --chmod" in str(exc.value)
    assert "line 7" in str(exc.value)
    assert repr(value)[1:-1] in str(exc.value)
