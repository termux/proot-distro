# The extractor hashes a layer as it consumes it, and refuses one whose
# total does not match. The total has to be the *whole blob*: tarfile stops
# at the end-of-archive marker and never reads the compressed tail, so
# whatever pulls that tail through has to feed the hasher too.
#
# Regression: ghcr.io/btwiuse/omarchy-android:0.0.14, layer 79253aaa0235
# (523 bytes) failed to install with "does not match its digest", the
# digest it "read" being that of the blob's first 512 bytes.

import hashlib
import io
import os
import random
import struct
import tarfile
import zlib

import pytest

from _builders import dir_fd, make_tar_bytes
from proot_distro.helpers.docker.layers import apply_layer
from proot_distro.helpers.tar_extract import (
    _HashingReader, extract_tar_fd_to_rootfs,
)

# What tarfile's `r|*` reads first, to tell what the compression is.
SNIFF_BYTES = 512


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _go_style_gzip(raw):
    """gzip *raw* the way Go's compress/gzip (so docker, buildkit) lays it out.

    The deflate body ends at a sync flush and the stream is closed by a
    separate, empty, final stored block — five bytes — before the CRC and
    length. It is that closing run which sits past the point where tarfile
    stops reading.
    """
    co = zlib.compressobj(9, zlib.DEFLATED, -15)
    body = co.compress(raw) + co.flush(zlib.Z_SYNC_FLUSH)
    header = b"\x1f\x8b\x08\x00\x00\x00\x00\x00\x00\xff"
    trailer = struct.pack("<II", zlib.crc32(raw), len(raw) & 0xFFFFFFFF)
    return header + body + b"\x01\x00\x00\xff\xff" + trailer


def _layer_just_past_the_sniff():
    """A layer of 513-524 bytes whose tar ends inside the first 512.

    Incompressible content grows the blob by about a byte per byte, so
    scanning the size finds a length in the window whatever the zlib
    build compresses the header to.
    """
    for n in range(0, 2000):
        content = random.Random(7).randbytes(n)
        raw = make_tar_bytes([{"name": "tmp/f", "type": "file",
                               "data": content}])
        blob = _go_style_gzip(raw)
        if SNIFF_BYTES < len(blob) <= SNIFF_BYTES + 12:
            return blob, content
    pytest.fail("no layer landed just past the sniff read")


class _CountingReader:
    def __init__(self, data):
        self._fh = io.BytesIO(data)
        self.drawn = 0

    def read(self, size=-1):
        chunk = self._fh.read(size)
        self.drawn += len(chunk)
        return chunk


def _tar_alone_never_reads_the_tail(blob):
    """The premise: tarfile finishes without drawing the whole blob."""
    spy = _CountingReader(blob)
    with tarfile.open(fileobj=spy, mode="r|*") as tf:
        for member in tf:
            tf.extractfile(member).read()
    return spy.drawn < len(blob)


def test_drain_hashes_what_it_pulls_through():
    data = bytes(range(256)) * 40
    reader = _HashingReader(io.BytesIO(data))
    assert reader.read(100) == data[:100]

    reader.drain()

    assert reader.hasher.hexdigest() == _sha(data)


def test_layer_whose_tail_tarfile_never_asks_for_is_verified(tmp_path):
    blob, content = _layer_just_past_the_sniff()
    assert _tar_alone_never_reads_the_tail(blob), \
        "the layer no longer ends past tarfile's last read"
    path = tmp_path / "layer.tar.gz"
    path.write_bytes(blob)
    root = tmp_path / "rootfs"
    root.mkdir()

    fd = os.open(path, os.O_RDONLY)
    try:
        with dir_fd(root) as rfd:
            apply_layer(fd, rfd, digest="sha256:" + _sha(blob))
    finally:
        os.close(fd)

    assert (root / "tmp" / "f").read_bytes() == content


def test_a_tail_that_differs_from_the_digest_is_refused(tmp_path):
    # The unread tail counts: change one byte of it and the digest the
    # blob was named by no longer matches.
    blob, _ = _layer_just_past_the_sniff()
    tampered = blob[:-1] + bytes([blob[-1] ^ 0xFF])
    assert _tar_alone_never_reads_the_tail(tampered)
    path = tmp_path / "layer.tar.gz"
    path.write_bytes(tampered)
    root = tmp_path / "rootfs"
    root.mkdir()

    fd = os.open(path, os.O_RDONLY)
    try:
        with dir_fd(root) as rfd:
            with pytest.raises(RuntimeError, match="does not match its digest"):
                extract_tar_fd_to_rootfs(
                    fd, rfd, handle_whiteouts=True,
                    subject="layer", expected_sha256=_sha(blob),
                )
    finally:
        os.close(fd)
