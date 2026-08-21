# Size caps on the JSON documents this program keeps in its own state
# tree.
#
# Each of them -- a container's manifest.json, a session record, the
# build-cache index, a manifest-cache entry -- is written by this program
# and is a few kilobytes at most. The *file* is not this program's,
# though: on Termux the whole state tree sits under the $TERMUX_PREFIX
# bound read-write into every non-isolated container, so a running guest
# decided how many bytes `login`, `run`, `ps`, `list --image`,
# `clear-cache` and `build` each read into memory before finding out the
# document was nonsense. json.load() on a descriptor reads until the file
# ends.
#
# The oversized documents below are deliberately *valid* JSON, padded
# with whitespace inside the object: what is under test is the size of
# the read, not the parse. Without the cap every one of them loads and
# is reported as a perfectly good record.

import errno
import fcntl
import json
import os

import pytest

from proot_distro import paths, session, statedir
from proot_distro.arch import get_device_cpu_arch
from proot_distro.constants import SESSIONS_DIR
from proot_distro.helpers import build_cache
from proot_distro.helpers.docker import cache as docker_cache


HOST_ARCH = get_device_cpu_arch()
_OVER = statedir.MAX_STATE_JSON_BYTES + 1


def _write_padded(path, payload):
    """Write *payload* as JSON stretched past the cap with whitespace."""
    body = json.dumps(payload)
    assert body.endswith("}")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    block = b" " * (1 << 20)
    with open(path, "wb") as fh:
        fh.write(body[:-1].encode())
        written = fh.tell()
        while written < _OVER:
            written += fh.write(block[:min(len(block), _OVER - written)])
        fh.write(b"}")


def _sparse(path, size):
    """A file of *size* zero bytes that costs no disk to make."""
    with open(path, "wb") as fh:
        fh.truncate(size)


# --- the helper itself -----------------------------------------------------

def test_read_state_file_takes_a_file_exactly_at_the_limit(tmp_path):
    path = str(tmp_path / "at-limit")
    _sparse(path, statedir.MAX_STATE_JSON_BYTES)
    fd = os.open(path, os.O_RDONLY)
    try:
        assert len(statedir.read_state_file(fd)) == \
            statedir.MAX_STATE_JSON_BYTES
    finally:
        os.close(fd)


def test_read_state_file_refuses_one_byte_more(tmp_path):
    path = str(tmp_path / "over-limit")
    _sparse(path, _OVER)
    fd = os.open(path, os.O_RDONLY)
    try:
        with pytest.raises(OSError) as exc:
            statedir.read_state_file(fd)
        assert exc.value.errno == errno.EFBIG
    finally:
        os.close(fd)


def test_read_state_file_counts_bytes_drawn_not_st_size(tmp_path):
    # A sparse file reports a size it never has to store, so the cap has
    # to be about what comes back from read(2).
    path = str(tmp_path / "sparse")
    _sparse(path, _OVER)
    assert os.stat(path).st_blocks * 512 < statedir.MAX_STATE_JSON_BYTES
    fd = os.open(path, os.O_RDONLY)
    try:
        with pytest.raises(OSError):
            statedir.read_state_file(fd)
    finally:
        os.close(fd)


def test_read_state_file_honours_a_smaller_limit(tmp_path):
    path = str(tmp_path / "small")
    with open(path, "wb") as fh:
        fh.write(b"0123456789")
    fd = os.open(path, os.O_RDONLY)
    try:
        assert statedir.read_state_file(fd, limit=10) == b"0123456789"
        os.lseek(fd, 0, os.SEEK_SET)
        with pytest.raises(OSError):
            statedir.read_state_file(fd, limit=9)
    finally:
        os.close(fd)


# --- containers/<name>/manifest.json ---------------------------------------

def test_an_oversized_container_manifest_is_not_read(builders):
    builders.make_container("box", arch=HOST_ARCH)
    _write_padded(paths.container_manifest("box"), {
        "image_ref": "evil:1", "arch": HOST_ARCH,
        "image_config": {"config": {"Cmd": ["/bin/sh"]}},
    })

    with pytest.raises(OSError) as exc:
        paths.read_container_manifest("box")
    assert exc.value.errno == errno.EFBIG
    # The forgiving form answers what it answers for any unreadable one.
    assert paths.container_image_config("box") == {}


def test_a_normal_container_manifest_is_still_read(builders):
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": "real:1", "arch": HOST_ARCH,
        "image_config": {"config": {"Cmd": ["/bin/sh"]}},
    })
    assert paths.read_container_manifest("box")["image_ref"] == "real:1"


# --- sessions/<pid>.json ---------------------------------------------------

def test_an_oversized_session_record_is_not_a_session():
    # Held, the way a guest that composed it would hold it: an unheld
    # entry is pruned whatever it contains, which would hide the point.
    name = os.path.join(SESSIONS_DIR, "4321.json")
    _write_padded(name, {
        "pid": 4321, "container": "box", "kind": "login",
        "command": ["/bin/sh"], "user": "root", "start_time": 1.0,
        "isolated": False, "minimal": False, "detach": False,
    })
    holder = open(name, "r")
    try:
        fcntl.flock(holder.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert session.active_sessions() == []
    finally:
        holder.close()


# --- build_cache_index.json ------------------------------------------------

def test_an_oversized_build_index_is_a_read_failure_not_an_empty_one():
    # The distinction matters: `clear-cache --orphan` treats the index as
    # a set of roots, so "unreadable" has to stop the sweep where "pins
    # nothing" would collect every blob a build still depends on.
    _write_padded(build_cache.index_path(), {
        "version": 1,
        "entries": {"abc": {"layer_digest": "sha256:" + "1" * 64}},
    })

    digests, readable = build_cache.recorded_layer_digests()
    assert digests == set()
    assert readable is False
    assert build_cache.lookup("abc") is None


def test_a_normal_build_index_is_still_read():
    build_cache.record("abc", "sha256:" + "1" * 64, "sha256:" + "2" * 64, 10)
    digests, readable = build_cache.recorded_layer_digests()
    assert readable is True
    assert digests == {"sha256:" + "1" * 64}


# --- oci_manifests/<key> ---------------------------------------------------

def _entry(ref):
    return {
        "image_ref": ref, "arch": HOST_ARCH, "repo": "library/x",
        "image_config": {},
        "manifest": {"schemaVersion": 2, "layers": [],
                     "config": {"digest": "sha256:" + "0" * 64}},
    }


def test_an_oversized_manifest_cache_entry_is_skipped():
    docker_cache.save_manifest_cache(
        "good:1", HOST_ARCH, _entry("good:1")["manifest"],
        repo="library/good", image_config={},
    )
    _write_padded(docker_cache.manifest_cache_path("evil:1", HOST_ARCH),
                  _entry("evil:1"))

    refs = {rec["image_ref"] for rec in docker_cache.iter_cached_images()}
    assert refs == {"good:1"}


def test_an_oversized_entry_is_reported_to_the_layer_sweep():
    # referenced_blob_digests() is the sweep's root set, so an entry it
    # cannot read has to be *reported* rather than skipped:
    # under-reporting there is `clear-cache --orphan` deleting blobs a
    # cached image still needs.
    path = docker_cache.manifest_cache_path("evil:1", HOST_ARCH)
    _write_padded(path, _entry("evil:1"))

    digests, unreadable = docker_cache.referenced_blob_digests()
    assert digests == set()
    assert unreadable == [path]
