# Containment tests for proot-distro's link2symlink handling.
#
# proot's --link2symlink extension emulates hard links with a two-hop
# symlink chain, and `backup` / the build layer writer must follow it to
# pack the backing file's content. A guest can create the same shape with
# two `ln -s` calls, so the chain is guest-controlled input: what it ends
# at has to be inside the rootfs, or a host file's bytes land in the
# archive (backup) or in a layer `push` uploads to a registry (build).

import io
import os
import tarfile

import pytest

from proot_distro import l2s
from proot_distro.commands.backup import _add_path
from proot_distro.helpers.layer_diff import _add_entry, _ParentFds


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "rootfs"
    (root / ".l2s").mkdir(parents=True)
    secret = tmp_path / "host_secret"
    secret.write_text("SECRET")
    return str(root), secret


def _pack(fn):
    """Run *fn* against a fresh tar and return its single member."""
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tf:
        fn(tf)
    bio.seek(0)
    with tarfile.open(fileobj=bio) as tf:
        members = tf.getmembers()
        assert len(members) == 1
        m = members[0]
        return m, (tf.extractfile(m).read() if m.isreg() else None)


def _backup_member(root, rel):
    # _add_path is addressed as (dir_fd, name) now; the walk normally
    # supplies both along with the lstat it already took.
    parent = os.path.join(root, os.path.dirname(rel)) if os.path.dirname(rel) \
        else root
    name = os.path.basename(rel)
    fd = os.open(parent, os.O_RDONLY | os.O_DIRECTORY)
    try:
        st = os.stat(name, dir_fd=fd, follow_symlinks=False)
        return _pack(lambda tf: _add_path(
            tf, fd, name, f"box/rootfs/{rel}",
            os.path.join(root, rel), st, root))
    finally:
        os.close(fd)


def _layer_member(root, rel):
    # _add_entry addresses the entry as (dir_fd, name) now; _ParentFds is
    # what the packer normally carries the parent descriptors in.
    parents = _ParentFds(root)
    try:
        return _pack(lambda tf: _add_entry(tf, parents, root, rel))
    finally:
        parents.close()


def _plant_escape(root, secret):
    """The two `ln -s` calls a guest needs: chain out of the rootfs."""
    os.symlink(".proot.l2s.evil0001", os.path.join(root, "innocent"))
    os.symlink(str(secret), os.path.join(root, ".proot.l2s.evil0001"))


def _plant_legit(root):
    """A real l2s chain: intermediate -> backing file, both in the rootfs."""
    backing = os.path.join(root, ".l2s", ".proot.l2s.a0001.0001")
    with open(backing, "w") as fh:
        fh.write("REAL")
    os.symlink(".proot.l2s.a0001.0001",
               os.path.join(root, ".l2s", ".proot.l2s.a0001"))
    os.symlink(os.path.join(root, ".l2s", ".proot.l2s.a0001"),
               os.path.join(root, "linked"))


def test_resolve_rejects_chain_leaving_rootfs(env):
    # The first hop lands inside the rootfs, so a lexical normpath check
    # passed it; the second hop is what leaves.
    root, secret = env
    _plant_escape(root, secret)
    link = os.path.join(root, "innocent")
    assert l2s.resolve_l2s_target(
        link, os.readlink(link), root) is None


def test_resolve_returns_the_backing_file_for_a_real_chain(env):
    root, _secret = env
    _plant_legit(root)
    link = os.path.join(root, "linked")
    resolved = l2s.resolve_l2s_target(link, os.readlink(link), root)
    assert resolved == os.path.realpath(
        os.path.join(root, ".l2s", ".proot.l2s.a0001.0001"))


def test_backup_does_not_pack_a_host_file(env):
    root, secret = env
    _plant_escape(root, secret)
    member, data = _backup_member(root, "innocent")
    # Stored as the symlink it is, never as the host file's content.
    assert member.issym()
    assert data is None
    assert member.linkname == ".proot.l2s.evil0001"
    assert secret.read_text() == "SECRET"


def test_layer_writer_does_not_pack_a_host_file(env):
    root, secret = env
    _plant_escape(root, secret)
    member, data = _layer_member(root, "innocent")
    assert member.issym()
    assert data is None


@pytest.mark.parametrize("pack", [_backup_member, _layer_member])
def test_real_l2s_chain_still_inlined(env, pack):
    root, _secret = env
    _plant_legit(root)
    member, data = pack(root, "linked")
    assert member.isreg()
    assert data == b"REAL"


def test_open_l2s_backing_refuses_a_component_outside_the_rootfs(env):
    # The opener is the second half of the guarantee: even handed a path
    # that sits outside, it walks from the rootfs fd and refuses.
    root, secret = env
    assert l2s.open_l2s_backing(root, str(secret)) is None


def test_open_l2s_backing_refuses_a_swapped_symlink(env):
    # A component re-pointed between the resolve and the read. The walk is
    # O_NOFOLLOW, so this fails rather than following it to the host.
    root, secret = env
    inner = os.path.join(root, "sub")
    os.mkdir(inner)
    resolved = os.path.join(inner, ".proot.l2s.x0001")
    with open(resolved, "w") as fh:
        fh.write("ok")
    assert l2s.open_l2s_backing(root, resolved) is not None
    # Now swap the parent directory for a symlink pointing out.
    os.remove(resolved)
    os.rmdir(inner)
    os.symlink(str(secret.parent), inner)
    assert l2s.open_l2s_backing(root, resolved) is None


def test_open_l2s_backing_refuses_a_fifo(env):
    # A FIFO under the name would block the read waiting for a peer the
    # guest never supplies.
    root, _secret = env
    path = os.path.join(root, ".proot.l2s.fifo0001")
    os.mkfifo(path)
    assert l2s.open_l2s_backing(root, path) is None
