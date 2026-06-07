# Tests for proot_distro.l2s — proot link2symlink target detection and the
# rename-time symlink rewriter.

import os
import signal

from proot_distro import l2s


def test_resolve_relative_target_inside_rootfs(tmp_path):
    root = tmp_path / "rootfs"
    (root / ".l2s").mkdir(parents=True)
    (root / "app").mkdir()
    backing = root / ".l2s" / ".proot.l2s.thing0001"
    backing.write_text("data")
    symlink = root / "app" / "link"
    target = "../.l2s/.proot.l2s.thing0001"
    resolved = l2s.resolve_l2s_target(str(symlink), target, str(root))
    assert resolved == os.path.normpath(str(backing))


def test_resolve_absolute_target_inside_rootfs(tmp_path):
    root = tmp_path / "rootfs"
    (root / ".l2s").mkdir(parents=True)
    backing = root / ".l2s" / ".l2s.x0002"
    backing.write_text("d")
    symlink = root / "x"
    resolved = l2s.resolve_l2s_target(str(symlink), str(backing), str(root))
    assert resolved == os.path.normpath(str(backing))


def test_resolve_non_l2s_target_returns_none(tmp_path):
    root = tmp_path / "rootfs"
    root.mkdir()
    symlink = root / "link"
    assert l2s.resolve_l2s_target(str(symlink), "../etc/passwd", str(root)) is None


def test_resolve_l2s_target_escape_returns_none(tmp_path):
    # An l2s-prefixed name whose resolved path escapes the rootfs is rejected.
    root = tmp_path / "rootfs"
    (root / "a").mkdir(parents=True)
    symlink = root / "a" / "link"
    target = "../../.proot.l2s.evil0001"  # resolves to tmp_path, outside root
    assert l2s.resolve_l2s_target(str(symlink), target, str(root)) is None


def test_rewrite_l2s_targets(tmp_path):
    root = tmp_path / "rootfs"
    (root / "sub").mkdir(parents=True)
    old_prefix = "/old/install/path/rootfs"
    match = root / "sub" / "matched"
    other = root / "sub" / "other"
    os.symlink(old_prefix + "/.l2s/file0001", str(match))
    os.symlink("/somewhere/else", str(other))

    before_int = signal.getsignal(signal.SIGINT)
    l2s.rewrite_l2s_targets(str(root), old_prefix)
    after_int = signal.getsignal(signal.SIGINT)

    # Matching target re-rooted; non-matching untouched.
    assert os.readlink(str(match)) == str(root) + "/.l2s/file0001"
    assert os.readlink(str(other)) == "/somewhere/else"
    # Signal handlers restored after the walk.
    assert after_int == before_int
