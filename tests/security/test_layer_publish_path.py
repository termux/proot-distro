# Containment tests for where a build puts what it produces.
#
# A layer blob is named by the digest of its own bytes, so it is packed
# into the build's scratch directory and renamed into the layer cache
# afterwards. That rename used to be preceded by
# os.makedirs(os.path.dirname(final)), which accepts a symlink to a
# directory, and os.replace() then resolved the same name -- so a guest
# that left `cache/oci_layers -> <host dir>` behind (the cache is under
# the $TERMUX_PREFIX bound read-write into every non-isolated container)
# collected every layer a build produced. The build's own scratch root
# had the same shape: `build-tmp` is a predictable name and mkdtemp()
# resolved it.

import errno
import os
import shutil

import pytest

from proot_distro import dirfd, statedir
from proot_distro.atomic import publish_file
from proot_distro.commands.build import _make_build_tmp
from proot_distro.constants import LAYER_CACHE_DIR, RUNTIME_DIR


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    (d / "keepsake").write_text("host content\n")
    return d


@pytest.fixture
def blob(tmp_path):
    src = tmp_path / "layer-0-0.tar.gz"
    src.write_bytes(b"layer bytes")
    return str(src)


def test_publish_lands_in_the_cache(blob):
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    publish_file(blob, dest)
    assert open(dest, "rb").read() == b"layer bytes"
    assert not os.path.exists(blob)


def test_publish_refuses_a_symlinked_layer_cache(blob, outside):
    os.rmdir(LAYER_CACHE_DIR)
    os.symlink(str(outside), LAYER_CACHE_DIR)

    with pytest.raises(OSError) as exc:
        publish_file(blob, os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef"))
    assert exc.value.errno == errno.ENOTDIR
    assert sorted(os.listdir(str(outside))) == ["keepsake"]
    # The blob is still where the build left it, not somewhere else.
    assert open(blob, "rb").read() == b"layer bytes"


def test_publish_replaces_a_planted_blob_name(blob, outside):
    victim = outside / "host-file"
    victim.write_text("host content\n")
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    os.symlink(str(victim), dest)

    publish_file(blob, dest)

    assert victim.read_text() == "host content\n"
    assert not os.path.islink(dest)
    assert open(dest, "rb").read() == b"layer bytes"


def test_publish_creates_missing_cache_levels(blob):
    shutil.rmtree(LAYER_CACHE_DIR)
    dest = os.path.join(LAYER_CACHE_DIR, "sha256_deadbeef")
    publish_file(blob, dest)
    assert os.path.isfile(dest)


def test_publish_leaves_a_user_destination_alone(blob, tmp_path, outside):
    link = tmp_path / "link"
    os.symlink(str(outside), str(link))
    publish_file(blob, str(link / "archive.tar"))
    assert (outside / "archive.tar").read_bytes() == b"layer bytes"


# --- the build's scratch root ----------------------------------------------

def test_build_tmp_is_made_inside_the_runtime_tree():
    root, fd = _make_build_tmp()
    try:
        assert os.path.dirname(root) == os.path.join(RUNTIME_DIR, "build-tmp")
        assert os.path.isdir(root)
        assert statedir.is_state_path(root)
        # The descriptor names the directory that was just created, not
        # the name it was created under.
        assert os.stat(root).st_ino == os.fstat(fd).st_ino
    finally:
        os.close(fd)
        statedir.remove_state_tree(root)


def test_build_tmp_does_not_follow_a_planted_name(outside):
    os.symlink(str(outside), os.path.join(RUNTIME_DIR, "build-tmp"))
    root, fd = _make_build_tmp()
    try:
        # Refused, and the build falls back to the system temp dir the
        # way it always did when the runtime tree could not hold one.
        assert not root.startswith(RUNTIME_DIR + os.sep)
        assert sorted(os.listdir(str(outside))) == ["keepsake"]
    finally:
        os.close(fd)
        shutil.rmtree(root, ignore_errors=True)


def test_build_tmp_roots_are_distinct():
    (first, first_fd), (second, second_fd) = (
        _make_build_tmp(), _make_build_tmp(),
    )
    try:
        assert first != second
    finally:
        os.close(first_fd)
        os.close(second_fd)
        statedir.remove_state_tree(first)
        statedir.remove_state_tree(second)


def test_build_tmp_descriptor_survives_the_name_being_re_pointed(outside):
    # What the descriptor is for: the run directory's name sits in a
    # world the invoking user can write, and a RUN step's leftovers are
    # the invoking user.
    root, fd = _make_build_tmp()
    try:
        os.rename(root, root + ".moved")
        os.symlink(str(outside), root)
        try:
            sub = dirfd.descend_at(fd, ("stage-0",), create=True)
            os.close(sub)
            assert os.path.isdir(os.path.join(root + ".moved", "stage-0"))
            assert sorted(os.listdir(str(outside))) == ["keepsake"]
        finally:
            os.unlink(root)
    finally:
        os.close(fd)
        statedir.remove_state_tree(root + ".moved")
