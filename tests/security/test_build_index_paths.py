# Containment tests for the build-cache index and the lock file next to it.
#
# Both live in the download cache, which is guest-writable on Termux --
# it sits under the $TERMUX_PREFIX bound read-write into every
# non-isolated container -- and both names are entirely predictable. The
# lock was created with os.makedirs(dirname) + os.open(O_RDWR|O_CREAT) on
# the name, so a planted symlink had this program create the file it
# named, and a planted FIFO blocked the build until a peer that never
# comes. The index itself was read with open(path), with the same FIFO
# problem; every *write* already went through atomic_replace.

import errno
import os
import signal

import pytest

from proot_distro.helpers import build_cache


@pytest.fixture
def victim(tmp_path):
    path = tmp_path / "host-file"
    path.write_text("host content\n")
    return path


def _lock_path():
    return build_cache.index_path() + ".lock"


def _alarm(seconds=5):
    """Fail loudly instead of hanging, should an open ever block again."""
    def _boom(_sig, _frm):
        raise AssertionError("open blocked")
    old = signal.signal(signal.SIGALRM, _boom)
    signal.alarm(seconds)
    return old


def _disarm(old):
    signal.alarm(0)
    signal.signal(signal.SIGALRM, old)


# --- the lock file ---------------------------------------------------------

def test_lock_does_not_create_through_a_symlink(victim):
    target = victim.parent / "not-yet-there"
    os.symlink(str(target), _lock_path())

    build_cache.record("h" * 64, "sha256:aa", "sha256:bb", 3)

    assert not target.exists()
    assert not os.path.islink(_lock_path())
    assert build_cache.lookup("h" * 64)["layer_digest"] == "sha256:aa"


def test_lock_does_not_open_an_existing_host_file(victim):
    os.symlink(str(victim), _lock_path())

    build_cache.record("h" * 64, "sha256:aa", "sha256:bb", 3)

    assert victim.read_text() == "host content\n"
    assert not os.path.islink(_lock_path())


def test_lock_does_not_block_on_a_planted_fifo():
    os.mkfifo(_lock_path())
    old = _alarm()
    try:
        build_cache.record("h" * 64, "sha256:aa", "sha256:bb", 3)
    finally:
        _disarm(old)
    assert build_cache.lookup("h" * 64) is not None


def test_lock_file_is_reused_not_recreated():
    build_cache.record("a" * 64, "sha256:aa", "sha256:bb", 1)
    first = os.stat(_lock_path()).st_ino
    build_cache.record("b" * 64, "sha256:cc", "sha256:dd", 2)
    assert os.stat(_lock_path()).st_ino == first


# --- the index -------------------------------------------------------------

def test_index_read_does_not_block_on_a_planted_fifo():
    os.mkfifo(build_cache.index_path())
    old = _alarm()
    try:
        assert build_cache.lookup("a" * 64) is None
        digests, readable = build_cache.recorded_layer_digests()
    finally:
        _disarm(old)
    assert digests == set()
    # A pipe is not an index that pins nothing; it is an index that
    # cannot be read, and the layer sweep must not collect on that basis.
    assert readable is False


def test_index_read_refuses_a_symlink(victim):
    victim.write_text('{"version": 1, "entries": {}}')
    os.symlink(str(victim), build_cache.index_path())

    assert build_cache.lookup("a" * 64) is None
    assert build_cache.recorded_layer_digests() == (set(), False)


def test_missing_index_pins_nothing_and_is_readable():
    assert build_cache.recorded_layer_digests() == (set(), True)


def test_discard_removes_the_link_not_its_target(victim):
    os.symlink(str(victim), build_cache.index_path())
    removed, _size = build_cache.discard_index()
    assert removed
    assert not os.path.lexists(build_cache.index_path())
    assert victim.read_text() == "host content\n"


def test_discard_reports_an_absent_index():
    assert build_cache.discard_index() == (False, 0)


def test_discard_refuses_a_planted_cache_root(monkeypatch, tmp_path):
    import shutil

    from proot_distro import statedir
    from proot_distro.constants import BASE_CACHE_DIR

    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setattr(
        statedir, "STATE_ROOTS", (os.path.dirname(BASE_CACHE_DIR),))
    shutil.rmtree(BASE_CACHE_DIR)
    os.symlink(str(outside), BASE_CACHE_DIR)
    try:
        with pytest.raises(OSError) as exc:
            build_cache.discard_index()
        assert exc.value.errno == errno.ENOTDIR
    finally:
        os.unlink(BASE_CACHE_DIR)
        os.makedirs(BASE_CACHE_DIR, exist_ok=True)
