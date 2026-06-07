# Tests for proot_distro.atomic.atomic_replace — the write-tmp-then-rename
# context manager every cache writer relies on.

import os

import pytest

from proot_distro.atomic import atomic_replace


def test_success_renames_into_place(tmp_path):
    dest = tmp_path / "out.txt"
    with atomic_replace(str(dest)) as tmp:
        assert tmp != str(dest)
        assert os.path.dirname(tmp) == str(tmp_path)
        with open(tmp, "w") as fh:
            fh.write("hello")
    assert dest.read_text() == "hello"
    # The tmp file is gone (renamed away).
    assert os.listdir(tmp_path) == ["out.txt"]


def test_creates_missing_dest_dir(tmp_path):
    dest = tmp_path / "nested" / "deep" / "out.bin"
    with atomic_replace(str(dest)) as tmp:
        with open(tmp, "wb") as fh:
            fh.write(b"\x00\x01")
    assert dest.read_bytes() == b"\x00\x01"


def test_exception_removes_tmp_and_reraises(tmp_path):
    dest = tmp_path / "out.txt"
    dest.write_text("original")
    with pytest.raises(ValueError):
        with atomic_replace(str(dest)) as tmp:
            with open(tmp, "w") as fh:
                fh.write("partial")
            raise ValueError("boom")
    # Original untouched, no tmp left behind.
    assert dest.read_text() == "original"
    assert sorted(os.listdir(tmp_path)) == ["out.txt"]


def test_keyboardinterrupt_cleans_up(tmp_path):
    dest = tmp_path / "out.txt"
    with pytest.raises(KeyboardInterrupt):
        with atomic_replace(str(dest)) as tmp:
            with open(tmp, "w") as fh:
                fh.write("partial")
            raise KeyboardInterrupt()
    assert not dest.exists()
    assert os.listdir(tmp_path) == []


def test_unique_tmp_names(tmp_path):
    dest = tmp_path / "out.txt"
    seen = []
    with atomic_replace(str(dest)) as tmp1:
        seen.append(tmp1)
        with atomic_replace(str(dest)) as tmp2:
            seen.append(tmp2)
            with open(tmp2, "w") as fh:
                fh.write("b")
        with open(tmp1, "w") as fh:
            fh.write("a")
    assert seen[0] != seen[1]
