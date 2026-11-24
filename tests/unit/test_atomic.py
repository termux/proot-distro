# Tests for proot_distro.atomic — the write-tmp-then-rename context
# manager every cache writer relies on.
#
# It yields the temporary's *descriptor*, never its name: a name the
# caller has to open again is a name something else can put a symlink
# under in between.

import os
import signal
import threading

import pytest

from proot_distro import atomic
from proot_distro.atomic import atomic_replace, atomic_write
from proot_distro.constants import RUNTIME_DIR


def test_success_renames_into_place(tmp_path):
    dest = tmp_path / "out.txt"
    with atomic_write(str(dest), "w") as fh:
        fh.write("hello")
    assert dest.read_text() == "hello"
    # The tmp file is gone (renamed away).
    assert os.listdir(tmp_path) == ["out.txt"]


def test_creates_missing_dest_dir(tmp_path):
    dest = tmp_path / "nested" / "deep" / "out.bin"
    with atomic_write(str(dest)) as fh:
        fh.write(b"\x00\x01")
    assert dest.read_bytes() == b"\x00\x01"


def test_raw_descriptor_is_writable_and_readable(tmp_path):
    dest = tmp_path / "out.bin"
    with atomic_replace(str(dest)) as fd:
        os.write(fd, b"raw")
        os.lseek(fd, 0, os.SEEK_SET)
        assert os.read(fd, 16) == b"raw"
    assert dest.read_bytes() == b"raw"


def test_exception_removes_tmp_and_reraises(tmp_path):
    dest = tmp_path / "out.txt"
    dest.write_text("original")
    with pytest.raises(ValueError):
        with atomic_write(str(dest), "w") as fh:
            fh.write("partial")
            raise ValueError("boom")
    # Original untouched, no tmp left behind.
    assert dest.read_text() == "original"
    assert sorted(os.listdir(tmp_path)) == ["out.txt"]


def test_keyboardinterrupt_cleans_up(tmp_path):
    dest = tmp_path / "out.txt"
    with pytest.raises(KeyboardInterrupt):
        with atomic_write(str(dest), "w") as fh:
            fh.write("partial")
            raise KeyboardInterrupt()
    assert not dest.exists()
    assert os.listdir(tmp_path) == []


@pytest.mark.skipif(
    not hasattr(signal, "pthread_sigmask")
    or not hasattr(signal, "pthread_kill"),
    reason="needs thread-directed signal masking",
)
def test_sigint_during_host_temporary_creation_is_cleaned(
        tmp_path, monkeypatch):
    real_mkstemp = atomic.tempfile.mkstemp

    def create_then_interrupt(*args, **kwargs):
        result = real_mkstemp(*args, **kwargs)
        signal.pthread_kill(threading.get_ident(), signal.SIGINT)
        return result

    monkeypatch.setattr(atomic.tempfile, "mkstemp", create_then_interrupt)
    destination = tmp_path / "out.txt"

    with pytest.raises(KeyboardInterrupt):
        with atomic_replace(str(destination)):
            pytest.fail("pending SIGINT was not delivered")

    assert not destination.exists()
    assert os.listdir(tmp_path) == []


@pytest.mark.skipif(
    not hasattr(signal, "pthread_sigmask")
    or not hasattr(signal, "pthread_kill"),
    reason="needs thread-directed signal masking",
)
def test_sigint_during_state_temporary_creation_is_cleaned(monkeypatch):
    real_open = atomic.dirfd.open_new_at

    def create_then_interrupt(*args, **kwargs):
        result = real_open(*args, **kwargs)
        signal.pthread_kill(threading.get_ident(), signal.SIGINT)
        return result

    monkeypatch.setattr(atomic.dirfd, "open_new_at", create_then_interrupt)
    destination_dir = os.path.join(RUNTIME_DIR, "atomic-interrupt")
    destination = os.path.join(destination_dir, "out.txt")

    with pytest.raises(KeyboardInterrupt):
        with atomic_replace(destination):
            pytest.fail("pending SIGINT was not delivered")

    assert not os.path.exists(destination)
    assert os.listdir(destination_dir) == []


def test_unique_tmp_names(tmp_path):
    dest = tmp_path / "out.txt"
    with atomic_replace(str(dest)) as fd1:
        with atomic_replace(str(dest)) as fd2:
            assert len(os.listdir(tmp_path)) == 2
            os.write(fd2, b"b")
        os.write(fd1, b"a")
    assert dest.read_bytes() == b"a"
