# ADD reads content nobody local chose the size of: an HTTP response, and
# every regular member of an archive it auto-extracts.
#
# A file_map covers a whole COPY/ADD instruction and is only consumed once
# the instruction ends — the tree is materialised from it and then the layer
# is packed from it — so an entry holding bytes holds them for the length of
# the instruction, and every entry of an extracted archive holds them at the
# same time. A single ADD of a large tarball was enough to take the build
# process out. Content that is not already a file is spooled to one.

import io
import os

import pytest

from proot_distro.helpers.build_engine import copy_step


@pytest.fixture
def spool(tmp_path):
    d = tmp_path / "spool"
    d.mkdir()
    return str(d)


def _assert_no_bytes_held(file_map):
    """Every entry names a file on disk; none carries a payload."""
    for arcname, entry in file_map.items():
        assert "data" not in entry, f"{arcname} holds its content in memory"
        assert entry["kind"] != "content", arcname
        if entry["kind"] == "file":
            assert os.path.isfile(entry["src"]), arcname


def test_auto_extracted_members_are_spooled(tmp_path, builders, spool):
    arc = tmp_path / "payload.tar"
    builders.make_tar(str(arc), [
        {"name": "a", "type": "file", "data": b"A" * 4096},
        {"name": "b", "type": "file", "data": b"B" * 4096},
        {"name": "d", "type": "dir"},
        {"name": "d/link", "type": "symlink", "linkname": "../a"},
    ])
    file_map = {}
    copy_step._extract_tar_into_dest(
        str(arc), "extracted", file_map, 0, 0, spool)

    _assert_no_bytes_held(file_map)
    with open(file_map["extracted/a"]["src"], "rb") as fh:
        assert fh.read() == b"A" * 4096
    # Each member gets its own spool file rather than sharing one.
    assert (file_map["extracted/a"]["src"]
            != file_map["extracted/b"]["src"])
    # Non-content members still describe themselves inline.
    assert file_map["extracted/d"]["kind"] == "dir"
    assert file_map["extracted/d/link"]["kind"] == "symlink"


def test_member_mtime_survives_the_spool(tmp_path, builders, spool):
    # layer_diff's "file" kind reads mtime off the file, not off the entry,
    # so the archive's timestamp has to land on the spool file itself.
    arc = tmp_path / "payload.tar"
    builders.make_tar(str(arc), [
        {"name": "a", "type": "file", "data": b"A", "mtime": 1234567890},
    ])
    file_map = {}
    copy_step._extract_tar_into_dest(
        str(arc), "extracted", file_map, 0, 0, spool)

    assert int(os.stat(file_map["extracted/a"]["src"]).st_mtime) == 1234567890


def test_an_absurd_member_mtime_does_not_raise(tmp_path, builders, spool):
    # os.utime() raises OverflowError, not OSError, on a value the platform
    # cannot store — and the value comes out of an attacker-writable header.
    arc = tmp_path / "payload.tar"
    builders.make_tar(str(arc), [
        {"name": "a", "type": "file", "data": b"A", "mtime": 2 ** 63},
    ])
    file_map = {}
    copy_step._extract_tar_into_dest(
        str(arc), "extracted", file_map, 0, 0, spool)

    assert os.path.isfile(file_map["extracted/a"]["src"])


def test_url_response_is_spooled(spool):
    body = b"N" * (1 << 20)

    class _Resp(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    class _Opener:
        def open(self, url):
            return _Resp(body)

    file_map = {}
    orig = copy_step.urllib.request.build_opener
    copy_step.urllib.request.build_opener = lambda *a: _Opener()
    try:
        copy_step._copy_url(
            "https://example.invalid/blob.bin", "/opt/blob.bin",
            file_map, 0, 0, None, spool,
        )
    finally:
        copy_step.urllib.request.build_opener = orig

    _assert_no_bytes_held(file_map)
    with open(file_map["opt/blob.bin"]["src"], "rb") as fh:
        assert fh.read() == body
