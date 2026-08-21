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


def test_relax_permissions_does_not_chmod_a_hardlinked_host_file(env):
    root, secret = env
    # A hardlink is the file itself under a second name: O_NOFOLLOW says
    # nothing about it and the entry looks like any other rootfs file.
    secret.chmod(0o000)
    link = root / "f"
    os.link(str(secret), str(link))
    st = os.stat(str(link), follow_symlinks=False)

    fd = _dir_fd(root)
    try:
        backup_mod._relax_permissions(fd, "f", "box/rootfs/f",
                                      str(link), st)
    finally:
        os.close(fd)

    assert stat.S_IMODE(secret.stat().st_mode) == 0o000


def test_relax_permissions_does_not_chmod_a_link_made_after_the_lstat(env):
    root, secret = env
    # The walk's lstat says one link; the link appears before the chmod.
    # chmod_at re-reads the count off the descriptor it opens.
    secret.chmod(0o000)
    f = root / "f"
    f.write_text("x")
    f.chmod(0o000)
    st = os.stat(str(f), follow_symlinks=False)
    assert st.st_nlink == 1
    os.remove(str(f))
    os.link(str(secret), str(f))

    fd = _dir_fd(root)
    try:
        backup_mod._relax_permissions(fd, "f", "box/rootfs/f", str(f), st)
    finally:
        os.close(fd)

    assert stat.S_IMODE(secret.stat().st_mode) == 0o000


def test_relax_permissions_leaves_a_readable_hardlink_alone(env):
    # Nothing to relax: an ordinary hardlinked binary (busybox and
    # friends) is already owner-readable, so no warning and no chmod.
    root, secret = env
    secret.chmod(0o644)
    link = root / "f"
    os.link(str(secret), str(link))
    st = os.stat(str(link), follow_symlinks=False)

    fd = _dir_fd(root)
    try:
        backup_mod._relax_permissions(fd, "f", "box/rootfs/f",
                                      str(link), st)
    finally:
        os.close(fd)

    assert stat.S_IMODE(secret.stat().st_mode) == 0o644


def test_relax_permissions_names_the_entry_it_skipped(env, capsys):
    root, secret = env
    secret.chmod(0o000)
    os.link(str(secret), str(root / "f"))
    st = os.stat(str(root / "f"), follow_symlinks=False)

    fd = _dir_fd(root)
    try:
        backup_mod._relax_permissions(fd, "f", "box/rootfs/f",
                                      str(root / "f"), st)
    finally:
        os.close(fd)

    err = capsys.readouterr().err
    assert "box/rootfs/f" in err
    assert "hard link" in err


# --- the archiving pass ----------------------------------------------------

def _pack_one(root, name, path, st):
    """Archive one entry and return its members plus the first one's data."""
    bio = io.BytesIO()
    fd = _dir_fd(root)
    # The command pins the rootfs once and hands that descriptor down;
    # here the entry's parent is the rootfs itself.
    rootfs_fd = _dir_fd(root)
    try:
        with tarfile.open(fileobj=bio, mode="w") as tf:
            backup_mod._add_path(tf, fd, name, f"box/rootfs/{name}",
                                 path, st, str(root), rootfs_fd)
    finally:
        os.close(rootfs_fd)
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


# --- the container directory itself ----------------------------------------
#
# The three passes (relax, measure, archive) each reopened
# containers/<name> by name, after an installed check that had already
# walked it. That directory is guest-writable on Termux, and the shared
# lock is deliberately not enough to keep a live session out of it, so a
# swap in between aimed the whole command at a directory of the session's
# choosing: the permission pass chmod'ed what it found there and the
# archiver packed it.

@pytest.fixture
def planted(tmp_path, builders):
    """An installed container plus a host directory to redirect it to.

    Returns (name, host_dir, swap) where swap() replaces
    containers/<name> with a symlink to host_dir — the move the loser of
    the race makes.
    """
    from proot_distro.paths import container_dir

    builders.make_container("box", files={"/etc/hostname": b"real\n"})
    host = tmp_path / "host-dir"
    (host / "rootfs" / "etc").mkdir(parents=True)
    (host / "rootfs" / "etc" / "secret").write_text("SECRET")
    (host / "rootfs" / "etc" / "secret").chmod(0o600)

    def swap():
        real = container_dir("box") + ".moved"
        os.rename(container_dir("box"), real)
        os.symlink(str(host), container_dir("box"))

    return "box", host, swap


def _run_backup(name, out):
    from types import SimpleNamespace

    from proot_distro.commands.backup import command_backup

    command_backup(SimpleNamespace(
        container_name=name, output=str(out), compression=None,
        verbose=False,
    ))


def test_backup_refuses_a_container_dir_swapped_after_the_check(
        tmp_path, planted, monkeypatch):
    """Swapped between the installed check and the open: fail, don't follow."""
    name, host, swap = planted
    real_check = backup_mod.container_is_installed

    def _check_then_swap(container):
        answer = real_check(container)
        swap()
        return answer

    monkeypatch.setattr(backup_mod, "container_is_installed",
                        _check_then_swap)

    out = tmp_path / "bk.tar"
    with pytest.raises(SystemExit) as exc:
        _run_backup(name, out)
    assert exc.value.code == 1
    assert not out.exists()
    # The host tree was neither read nor relaxed.
    assert stat.S_IMODE(
        (host / "rootfs" / "etc" / "secret").stat().st_mode) == 0o600


def test_backup_holds_its_pin_across_a_mid_run_swap(tmp_path, planted,
                                                    monkeypatch):
    """Swapped while the passes run: the archive is still the real tree."""
    name, host, swap = planted
    real_fix = backup_mod._fix_permissions

    def _fix_then_swap(container_fd, rootfs_dir):
        real_fix(container_fd, rootfs_dir)
        swap()

    monkeypatch.setattr(backup_mod, "_fix_permissions", _fix_then_swap)

    out = tmp_path / "bk.tar"
    _run_backup(name, out)

    with tarfile.open(str(out)) as tf:
        names = tf.getnames()
        payload = tf.extractfile("box/rootfs/etc/hostname").read()
    assert payload == b"real\n"
    assert "box/rootfs/etc/secret" not in names
