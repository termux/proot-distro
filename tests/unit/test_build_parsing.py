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


def test_is_tar_archive_ustar(tmp_path, builders):
    p = tmp_path / "a.tar"
    builders.make_tar(str(p), [{"name": "x", "type": "file", "data": b"hi"}])
    assert parsing.is_tar_archive(str(p)) is True


def test_is_tar_archive_gzip(tmp_path, builders):
    p = tmp_path / "a.tar.gz"
    # Incompressible payload so the gzip header lands well past the 265-byte
    # minimum the signature probe requires.
    builders.make_tar(
        str(p),
        [{"name": "x", "type": "file", "data": os.urandom(4096)}],
        compression="gz",
    )
    assert parsing.is_tar_archive(str(p)) is True


def test_is_tar_archive_rejects_non_tar(tmp_path):
    p = tmp_path / "not.tar"
    p.write_bytes(b"this is just text, definitely not a tar archive" * 10)
    assert parsing.is_tar_archive(str(p)) is False


def test_is_tar_archive_short_file(tmp_path):
    p = tmp_path / "tiny"
    p.write_bytes(b"abc")
    assert parsing.is_tar_archive(str(p)) is False


def test_is_tar_archive_missing(tmp_path):
    assert parsing.is_tar_archive(str(tmp_path / "nope")) is False
