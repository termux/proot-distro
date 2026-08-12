# Unit tests for the zstd capability probe and the tarfile workarounds
# in proot_distro.compress.

import io
import os
import tarfile

import pytest

from proot_distro import compress


needs_zstd = pytest.mark.skipif(
    not compress.ZSTD_AVAILABLE,
    reason="interpreter has no zstd support (needs Python 3.14 + libzstd)",
)


def test_probe_agrees_with_tarfile():
    # The probe promises exactly what tarfile can actually do — a claim
    # the interpreter cannot back would surface as CompressionError deep
    # inside a backup instead of as a refusal up front.
    buf = io.BytesIO()
    if compress.ZSTD_AVAILABLE:
        assert "zst" in tarfile.TarFile.OPEN_METH
        with tarfile.open(fileobj=buf, mode="w|zst"):
            pass
        assert buf.getvalue().startswith(compress.ZSTD_MAGIC)
    else:
        with pytest.raises(tarfile.TarError):
            with tarfile.open(fileobj=buf, mode="w|zst"):
                pass


def test_header_and_file_sniffing(tmp_path):
    assert compress.header_is_zstd(compress.ZSTD_MAGIC + b"rest")
    assert not compress.header_is_zstd(b"\x1f\x8b\x08")
    assert not compress.header_is_zstd(b"")

    zst = tmp_path / "a.tar.zst"
    zst.write_bytes(compress.ZSTD_MAGIC + b"\x00" * 32)
    gz = tmp_path / "b.tar.gz"
    gz.write_bytes(b"\x1f\x8b\x08" + b"\x00" * 32)

    assert compress.file_is_zstd(str(zst))
    assert not compress.file_is_zstd(str(gz))
    # An unreadable path is not a zstd archive as far as this says: the
    # caller opens it next and reports the real error.
    assert not compress.file_is_zstd(str(tmp_path / "nope"))


def test_require_read_support_names_zstd(tmp_path, monkeypatch):
    monkeypatch.setattr(compress, "ZSTD_AVAILABLE", False)
    arc = tmp_path / "x.tar.zst"
    arc.write_bytes(compress.ZSTD_MAGIC + b"\x00" * 32)

    with pytest.raises(RuntimeError) as exc:
        compress.require_read_support(str(arc), "archive 'x.tar.zst'")
    text = str(exc.value)
    assert "zstd" in text
    # The diagnosis has to name the cause, not just the symptom.
    assert "3.14" in text or "libzstd" in text

    # A gzip archive is none of this function's business.
    gz = tmp_path / "x.tar.gz"
    gz.write_bytes(b"\x1f\x8b\x08" + b"\x00" * 32)
    compress.require_read_support(str(gz))


def test_require_read_support_silent_when_available(tmp_path, monkeypatch):
    monkeypatch.setattr(compress, "ZSTD_AVAILABLE", True)
    arc = tmp_path / "x.tar.zst"
    arc.write_bytes(compress.ZSTD_MAGIC + b"\x00" * 32)
    compress.require_read_support(str(arc))


def test_require_write_support(monkeypatch):
    monkeypatch.setattr(compress, "ZSTD_AVAILABLE", False)
    with pytest.raises(RuntimeError) as exc:
        compress.require_write_support()
    assert "zstd" in str(exc.value)


@needs_zstd
def test_tarfile_stream_mode_still_refuses_a_level():
    # The reason open_tar_writer exists. If CPython ever lifts this, the
    # workaround can go — this test is what will say so.
    with pytest.raises(ValueError):
        tarfile.open(fileobj=io.BytesIO(), mode="w|zst", compresslevel=1)


@needs_zstd
def test_open_tar_writer_applies_level_to_a_stream():
    payload = (b"proot-distro " * 4096) + bytes(range(256)) * 256

    def _write(level):
        buf = io.BytesIO()
        with compress.open_tar_writer(None, buf, level=level) as tf:
            info = tarfile.TarInfo("payload")
            info.size = len(payload)
            tf.addfile(info, io.BytesIO(payload))
        return buf.getvalue()

    low, high = _write(1), _write(19)
    assert low.startswith(compress.ZSTD_MAGIC)
    assert high.startswith(compress.ZSTD_MAGIC)
    # Levels reach a piped archive, which is what tarfile's own w|zst
    # cannot do.
    assert len(high) < len(low)

    with tarfile.open(fileobj=io.BytesIO(high), mode="r|*") as tf:
        member = next(iter(tf))
        assert member.name == "payload"
        assert tf.extractfile(member).read() == payload


@needs_zstd
def test_open_tar_writer_file_and_stream_agree(tmp_path):
    payload = b"same bytes either way" * 512
    out = tmp_path / "out.tar.zst"

    def _add(tf):
        info = tarfile.TarInfo("f")
        info.size = len(payload)
        info.mtime = 0
        tf.addfile(info, io.BytesIO(payload))

    with compress.open_tar_writer(str(out)) as tf:
        _add(tf)
    buf = io.BytesIO()
    with compress.open_tar_writer(None, buf) as tf:
        _add(tf)

    # One code path, one level: writing to a file and piping produce the
    # same archive rather than differing in compression level.
    assert out.read_bytes() == buf.getvalue()
    assert os.path.getsize(str(out)) > 0


@needs_zstd
def test_open_tar_writer_closes_in_order(tmp_path):
    # The frame must be finished and flushed before the file is closed,
    # or the archive is unreadable.
    out = tmp_path / "ordered.tar.zst"
    with compress.open_tar_writer(str(out)) as tf:
        info = tarfile.TarInfo("only")
        info.size = 3
        tf.addfile(info, io.BytesIO(b"abc"))
    with tarfile.open(str(out), "r:*") as tf:
        assert [m.name for m in tf.getmembers()] == ["only"]
