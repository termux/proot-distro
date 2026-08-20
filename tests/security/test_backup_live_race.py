# Containment tests for `backup`, which walks a rootfs a container may be
# using at the same time.
#
# backup takes only a *shared* container lock, on purpose: archiving a
# container should not require shutting its sessions down. That makes every
# path-based step in it two acts on two possibly-different files — a stat
# and then a chmod, an lstat and then an open — with a `login` session free
# to swap the name for a symlink in between. The walk now carries directory
# descriptors and names each entry as (dir_fd, name), so the swap is refused
# instead of followed.
#
# The swap is reproduced deterministically here rather than raced for: the
# stat the walk took is passed in, and the entry is replaced before the call
# that uses it — which is exactly the state the loser of the race is in.

import io
import os
import stat
import tarfile

import pytest

from proot_distro.commands import backup as backup_mod


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "rootfs"
    root.mkdir()
    secret = tmp_path / "host_secret"
    secret.write_text("SECRET")
    secret.chmod(0o600)
    return root, secret


def _dir_fd(path):
    return os.open(str(path), os.O_RDONLY | os.O_DIRECTORY)


def _swap_for_symlink(entry, target, mode=0o000):
    """Create *entry*, stat it, then replace it with a link to *target*.

    Returns the stat the walk would have taken a moment before the swap.
    """
    entry.write_text("original")
    entry.chmod(mode)
    st = os.stat(str(entry), follow_symlinks=False)
    os.remove(str(entry))
    os.symlink(str(target), str(entry))
    return st


# --- the permission pass ---------------------------------------------------

def test_relax_permissions_does_not_chmod_a_swapped_entrys_target(env):
    root, secret = env
    st = _swap_for_symlink(root / "f", secret)

    fd = _dir_fd(root)
    try:
        backup_mod._relax_permissions(fd, "f", "", str(root / "f"), st)
    finally:
        os.close(fd)

    assert stat.S_IMODE(secret.stat().st_mode) == 0o600


def test_relax_permissions_does_not_chmod_a_swapped_directorys_target(env):
    root, secret = env
    victim_dir = secret.parent / "host_dir"
    victim_dir.mkdir(mode=0o700)
    d = root / "d"
    d.mkdir(mode=0o000)
    st = os.stat(str(d), follow_symlinks=False)
    d.rmdir()
    os.symlink(str(victim_dir), str(d))

    fd = _dir_fd(root)
    try:
        backup_mod._relax_permissions(fd, "d", "", str(d), st)
    finally:
        os.close(fd)

    assert stat.S_IMODE(victim_dir.stat().st_mode) == 0o700


def test_relax_permissions_still_opens_a_sealed_entry(env):
    root, _secret = env
    f = root / "f"
    f.write_text("x")
    f.chmod(0o000)
    st = os.stat(str(f), follow_symlinks=False)

    fd = _dir_fd(root)
    try:
        backup_mod._relax_permissions(fd, "f", "", str(f), st)
    finally:
        os.close(fd)

    assert stat.S_IMODE(f.stat().st_mode) & stat.S_IRUSR


# --- the archiving pass ----------------------------------------------------

def _pack_one(root, name, path, st):
    """Archive one entry and return its members plus the first one's data."""
    bio = io.BytesIO()
    fd = _dir_fd(root)
    try:
        with tarfile.open(fileobj=bio, mode="w") as tf:
            backup_mod._add_path(tf, fd, name, f"box/rootfs/{name}",
                                 path, st, str(root))
    finally:
        os.close(fd)
    bio.seek(0)
    with tarfile.open(fileobj=bio) as tf:
        members = tf.getmembers()
        data = (tf.extractfile(members[0]).read()
                if members and members[0].isreg() else None)
        return members, data


def test_add_path_does_not_pack_a_swapped_entrys_target(env):
    root, secret = env
    st = _swap_for_symlink(root / "f", secret, mode=0o644)

    members, _data = _pack_one(root, "f", str(root / "f"), st)

    # O_NOFOLLOW refuses the link, so the entry is dropped rather than
    # standing in for the host file.
    assert members == []
    assert secret.read_text() == "SECRET"


def test_add_path_packs_an_ordinary_file(env):
    root, _secret = env
    (root / "f").write_text("payload")
    st = os.stat(str(root / "f"), follow_symlinks=False)

    members, data = _pack_one(root, "f", str(root / "f"), st)

    assert [m.name for m in members] == ["box/rootfs/f"]
    assert data == b"payload"
    assert members[0].uid == 0 and members[0].gid == 0
    assert members[0].uname == "" and members[0].gname == ""


def test_add_path_stores_a_plain_symlink_as_a_symlink(env):
    root, _secret = env
    os.symlink("etc/hostname", str(root / "link"))
    st = os.stat(str(root / "link"), follow_symlinks=False)

    members, _data = _pack_one(root, "link", str(root / "link"), st)

    assert members[0].issym()
    assert members[0].linkname == "etc/hostname"


def test_add_path_skips_a_fifo(env):
    root, _secret = env
    os.mkfifo(str(root / "pipe"))
    st = os.stat(str(root / "pipe"), follow_symlinks=False)

    members, _data = _pack_one(root, "pipe", str(root / "pipe"), st)

    assert members == []


def test_add_path_does_not_block_on_a_swapped_in_fifo(env):
    # The entry was a regular file when the walk stat'ed it and is a FIFO
    # by the time it is opened. open_regular_at()'s O_NONBLOCK is what keeps
    # that from hanging the backup until a peer that never comes shows up.
    root, _secret = env
    f = root / "f"
    f.write_text("original")
    st = os.stat(str(f), follow_symlinks=False)
    os.remove(str(f))
    os.mkfifo(str(f))

    members, _data = _pack_one(root, "f", str(f), st)

    assert members == []
