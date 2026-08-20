# Containment tests for atomic_replace()'s destination directory.
#
# Every cache / manifest / layer writer publishes through it, and it used
# to reach the destination directory by name: os.makedirs(exist_ok=True)
# accepts a symlink to a directory and tempfile.mkstemp(dir=...) resolves
# the same name again. The runtime and cache trees are guest-writable on
# Termux, so a planted `oci_layers -> <host dir>` redirected every blob
# written into that host directory.

import errno
import os
import stat

import pytest

from proot_distro import atomic
from proot_distro.atomic import atomic_replace
from proot_distro.constants import (
    BASE_CACHE_DIR, CONTAINERS_DIR, LAYER_CACHE_DIR, RUNTIME_DIR,
)


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    return d


def _write(path, data=b"payload"):
    with atomic_replace(path) as tmp:
        with open(tmp, "wb") as fh:
            fh.write(data)


# --- the state-tree branch -------------------------------------------------

def test_symlinked_cache_subdir_is_refused(outside):
    os.rmdir(LAYER_CACHE_DIR)
    os.symlink(str(outside), LAYER_CACHE_DIR)

    with pytest.raises(OSError) as exc:
        _write(os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef"))
    assert exc.value.errno == errno.ENOTDIR
    assert os.listdir(str(outside)) == []


def test_symlinked_intermediate_is_refused(outside):
    os.symlink(str(outside), os.path.join(CONTAINERS_DIR, "box"))

    with pytest.raises(OSError) as exc:
        _write(os.path.join(CONTAINERS_DIR, "box", "manifest.json"))
    assert exc.value.errno == errno.ENOTDIR
    assert os.listdir(str(outside)) == []


def test_file_in_the_way_of_a_parent_is_refused():
    with open(os.path.join(CONTAINERS_DIR, "box"), "w") as fh:
        fh.write("not a directory")
    with pytest.raises(OSError) as exc:
        _write(os.path.join(CONTAINERS_DIR, "box", "manifest.json"))
    assert exc.value.errno == errno.ENOTDIR


def test_missing_parents_are_created():
    dest = os.path.join(CONTAINERS_DIR, "box", "manifest.json")
    _write(dest, b"{}")
    assert open(dest, "rb").read() == b"{}"


def test_publish_replaces_a_symlinked_destination(outside):
    victim = outside / "host-file"
    victim.write_text("host content\n")
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    os.symlink(str(victim), dest)

    _write(dest, b"blob")

    assert victim.read_text() == "host content\n"
    assert not os.path.islink(dest)
    assert open(dest, "rb").read() == b"blob"


def test_temporary_is_removed_on_error():
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    with pytest.raises(RuntimeError):
        with atomic_replace(dest) as tmp:
            with open(tmp, "wb") as fh:
                fh.write(b"half")
            raise RuntimeError("boom")
    assert os.listdir(LAYER_CACHE_DIR) == []


def test_temporary_lives_next_to_the_destination():
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    with atomic_replace(dest) as tmp:
        assert os.path.dirname(tmp) == LAYER_CACHE_DIR
        assert os.path.basename(tmp).startswith("sha256_deadbeef.")
        assert os.path.exists(tmp)
        with open(tmp, "wb") as fh:
            fh.write(b"x")


def test_published_mode_is_owner_only():
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    _write(dest)
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600


def test_long_names_still_fit_one_component():
    name = "sha256_" + "a" * 240
    dest = os.path.join(LAYER_CACHE_DIR, name)
    with atomic_replace(dest) as tmp:
        assert len(os.fsencode(os.path.basename(tmp))) <= 255
        with open(tmp, "wb") as fh:
            fh.write(b"x")
    assert os.path.exists(dest)


def test_concurrent_writers_get_distinct_temporaries():
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    with atomic_replace(dest) as first:
        with atomic_replace(dest) as second:
            assert first != second
            with open(second, "wb") as fh:
                fh.write(b"2")
        with open(first, "wb") as fh:
            fh.write(b"1")
    assert open(dest, "rb").read() == b"1"


# --- the user-path branch --------------------------------------------------

def test_user_paths_are_left_alone(tmp_path, outside):
    # `backup -o` / `build --output` name their own destination; where the
    # user points it is not this program's business.
    link = tmp_path / "link"
    os.symlink(str(outside), str(link))
    dest = link / "archive.tar"
    _write(str(dest), b"archive")
    assert (outside / "archive.tar").read_bytes() == b"archive"


def test_state_location_recognises_both_roots():
    assert atomic._state_location(
        os.path.join(RUNTIME_DIR, "containers", "box", "x"))[0] == RUNTIME_DIR
    root, parts = atomic._state_location(
        os.path.join(BASE_CACHE_DIR, "oci_layers", "blob"))
    assert os.path.join(root, *parts) == os.path.join(
        BASE_CACHE_DIR, "oci_layers", "blob")
    assert atomic._state_location("/etc/passwd") == (None, None)
    # The root itself is not a destination.
    assert atomic._state_location(RUNTIME_DIR) == (None, None)
