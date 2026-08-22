# ADD reads content nobody local chose the size of: an HTTP response, and
# every regular member of an archive it auto-extracts.
#
# A file_map covers a whole COPY/ADD instruction and is only consumed once
# the instruction ends — the tree is materialised from it and then the layer
# is packed from it — so an entry holding bytes holds them for the length of
# the instruction, and every entry of an extracted archive holds them at the
# same time. A single ADD of a large tarball was enough to take the build
# process out. Content that is not already a file is spooled to one.

import http.client
import io
import os
from types import SimpleNamespace

import pytest

from proot_distro.helpers.build_engine import copy_step
from proot_distro.helpers.build_engine.errors import BuildError


@pytest.fixture
def spool(tmp_path):
    """The real _Spool, on a scratch root standing in for a build's.

    It owns a descriptor on the directory every spooled file is created
    in and read back through, so the tests hold one too rather than
    handing the module a path.
    """
    sp = copy_step._Spool(
        SimpleNamespace(tmp_root=str(tmp_path), tmp_root_fd=None)
    )
    yield sp
    sp.close()


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
    with open(str(arc), "rb") as fh:
        copy_step._extract_tar_into_dest(
            fh, "extracted", file_map, 0, 0, spool, "payload.tar")

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
    with open(str(arc), "rb") as fh:
        copy_step._extract_tar_into_dest(
            fh, "extracted", file_map, 0, 0, spool, "payload.tar")

    assert int(os.stat(file_map["extracted/a"]["src"]).st_mtime) == 1234567890


def test_an_absurd_member_mtime_does_not_raise(tmp_path, builders, spool):
    # os.utime() raises OverflowError, not OSError, on a value the platform
    # cannot store — and the value comes out of an attacker-writable header.
    arc = tmp_path / "payload.tar"
    builders.make_tar(str(arc), [
        {"name": "a", "type": "file", "data": b"A", "mtime": 2 ** 63},
    ])
    file_map = {}
    with open(str(arc), "rb") as fh:
        copy_step._extract_tar_into_dest(
            fh, "extracted", file_map, 0, 0, spool, "payload.tar")

    assert os.path.isfile(file_map["extracted/a"]["src"])


def test_url_response_is_spooled(spool):
    body = b"N" * (1 << 20)

    class _Resp(io.BytesIO):
        # A real response declares its length, and ADD now holds the
        # answer to it: short of that is a truncated download.
        headers = {"Content-Length": str(len(body))}

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


# --- a response that ends early --------------------------------------------
#
# ADD has no digest to check its download against, so a truncated body is
# not caught anywhere downstream: the short bytes go into the rootfs and
# into the layer `push` uploads, under the name of the whole file.


def _add_url(spool, resp_factory):
    file_map = {}
    class _Opener:
        def open(self, url):
            return resp_factory()

    orig = copy_step.urllib.request.build_opener
    copy_step.urllib.request.build_opener = lambda *a: _Opener()
    try:
        copy_step._copy_url(
            "https://example.invalid/blob.bin", "/opt/blob.bin",
            file_map, 0, 0, None, spool,
        )
    finally:
        copy_step.urllib.request.build_opener = orig
    return file_map


def test_a_body_short_of_its_declared_length_is_refused(spool):
    class _Short(io.BytesIO):
        headers = {"Content-Length": "1048576"}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    with pytest.raises(BuildError) as exc:
        _add_url(spool, lambda: _Short(b"N" * 4096))
    assert "4096 of 1048576" in str(exc.value)


def test_a_body_cut_mid_chunk_is_a_build_error(spool):
    # http.client.IncompleteRead is not an OSError: it used to walk out of
    # _copy_url, out of the engine, and out of command_build as a traceback.
    class _Cut(io.BytesIO):
        headers = {}

        def read(self, *a):
            raise http.client.IncompleteRead(b"partial", 900)

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    with pytest.raises(BuildError):
        _add_url(spool, lambda: _Cut(b""))
