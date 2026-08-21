# Containment tests for atomic_replace()'s destination directory and for
# the temporary it writes through.
#
# Every cache / manifest / layer writer publishes through it, and it used
# to reach the destination directory by name: os.makedirs(exist_ok=True)
# accepts a symlink to a directory and tempfile.mkstemp(dir=...) resolves
# the same name again. The runtime and cache trees are guest-writable on
# Termux, so a planted `oci_layers -> <host dir>` redirected every blob
# written into that host directory.
#
# The *temporary* was reached by name too: it was created, closed, and
# its path handed to the caller to open again. An unpredictable name
# cannot be waited for but it can be read out of readdir(), so a process
# sharing the directory replaced it with a symlink and the caller's
# open() wrote the file's bytes into whatever that named.

import errno
import os
import stat

import pytest

from proot_distro import atomic
from proot_distro.atomic import atomic_replace, atomic_write
from proot_distro.constants import (
    BASE_CACHE_DIR, CONTAINERS_DIR, LAYER_CACHE_DIR, RUNTIME_DIR,
)


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    return d


def _write(path, data=b"payload"):
    with atomic_write(path) as fh:
        fh.write(data)


def _temp_names(directory):
    """The in-flight temporaries sitting in *directory*."""
    return [n for n in os.listdir(directory) if ".tmp" in n]


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
        with atomic_write(dest) as fh:
            fh.write(b"half")
            raise RuntimeError("boom")
    assert os.listdir(LAYER_CACHE_DIR) == []


def test_temporary_lives_next_to_the_destination():
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    with atomic_replace(dest) as fd:
        names = _temp_names(LAYER_CACHE_DIR)
        assert len(names) == 1
        assert names[0].startswith("sha256_deadbeef.")
        # The descriptor is that entry, not merely a file beside it.
        entry = os.lstat(os.path.join(LAYER_CACHE_DIR, names[0]))
        written = os.fstat(fd)
        assert (entry.st_dev, entry.st_ino) == (written.st_dev,
                                                written.st_ino)
        os.write(fd, b"x")


def test_published_mode_is_owner_only():
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    _write(dest)
    assert stat.S_IMODE(os.stat(dest).st_mode) == 0o600


def test_long_names_still_fit_one_component():
    name = "sha256_" + "a" * 240
    dest = os.path.join(LAYER_CACHE_DIR, name)
    with atomic_replace(dest) as fd:
        for tmp in _temp_names(LAYER_CACHE_DIR):
            assert len(os.fsencode(tmp)) <= 255
        os.write(fd, b"x")
    assert os.path.exists(dest)


def test_concurrent_writers_get_distinct_temporaries():
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    with atomic_replace(dest) as first:
        with atomic_replace(dest) as second:
            assert len(set(_temp_names(LAYER_CACHE_DIR))) == 2
            os.write(second, b"2")
        os.write(first, b"1")
    assert open(dest, "rb").read() == b"1"


# --- the temporary itself --------------------------------------------------

def test_bytes_do_not_follow_a_swapped_temporary(outside):
    """A symlink planted under the temporary's name gets none of the write."""
    victim = outside / "host-file"
    victim.write_text("host content\n")
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")

    with pytest.raises(OSError) as exc:
        with atomic_replace(dest) as fd:
            # The concurrent same-UID writer reads the name out of
            # readdir() and puts its own link there.
            tmp = _temp_names(LAYER_CACHE_DIR)[0]
            os.unlink(os.path.join(LAYER_CACHE_DIR, tmp))
            os.symlink(str(victim), os.path.join(LAYER_CACHE_DIR, tmp))
            os.write(fd, b"payload the guest wanted somewhere else")

    assert exc.value.errno == errno.ESTALE
    # The bytes went into the unlinked inode, not through the link...
    assert victim.read_text() == "host content\n"
    # ...and the link was not published as the cache entry either.
    assert not os.path.lexists(dest)
    assert os.listdir(LAYER_CACHE_DIR) == []


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
