# Tests for proot_distro.progress — size formatting and the stream byte
# counter (the TTY-drawing functions are covered indirectly elsewhere).

import io

import pytest

from proot_distro import progress


@pytest.mark.parametrize("n,expected", [
    (0, "0 B"),
    (1, "1 B"),
    (1023, "1023 B"),
    (1024, "1.0 KiB"),
    (1536, "1.5 KiB"),
    (1 << 20, "1.0 MiB"),
    (3 * (1 << 20), "3.0 MiB"),
    (1 << 30, "1.0 GiB"),
    (5 * (1 << 30) + (1 << 29), "5.5 GiB"),
])
def test_fmt_size(n, expected):
    assert progress.fmt_size(n) == expected


def test_byte_counter_read_tally():
    src = io.BytesIO(b"abcdefghij")
    bc = progress.ByteCounter(src)
    assert bc.read(4) == b"abcd"
    assert bc.count == 4
    assert bc.read() == b"efghij"
    assert bc.count == 10


def test_byte_counter_readinto_tally():
    src = io.BytesIO(b"0123456789")
    bc = progress.ByteCounter(src)
    buf = bytearray(5)
    n = bc.readinto(buf)
    assert n == 5
    assert bytes(buf) == b"01234"
    assert bc.count == 5


def test_byte_counter_attr_passthrough():
    src = io.BytesIO(b"data")
    bc = progress.ByteCounter(src)
    # seek/tell are delegated to the wrapped object.
    bc.seek(0)
    assert bc.tell() == 0


def test_progress_active_false_without_tty():
    # Under pytest capture stderr is not a TTY.
    assert progress.progress_active() is False
