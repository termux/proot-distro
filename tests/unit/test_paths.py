# Tests for proot_distro.paths — container path layout, the `name:path`
# spec resolver (with traversal confinement), and lock-set construction.

import os

import pytest

from proot_distro import paths


def test_container_dir_layout(paths_mod=paths):
    from proot_distro.constants import CONTAINERS_DIR
    assert paths.container_dir("box") == os.path.join(CONTAINERS_DIR, "box")
    assert paths.container_rootfs("box") == os.path.join(
        CONTAINERS_DIR, "box", "rootfs"
    )
    assert paths.container_manifest("box") == os.path.join(
        CONTAINERS_DIR, "box", "manifest.json"
    )


def test_container_from_spec():
    assert paths.container_from_spec("box:/etc/hosts") == "box"
    assert paths.container_from_spec("/plain/host/path") is None
    assert paths.container_from_spec("box:") == "box"


def test_resolve_plain_path_is_abspath(tmp_path):
    rel = "some/./weird/../path"
    resolved = paths.resolve_container_path(rel)
    assert resolved == os.path.normpath(os.path.abspath(rel))
    assert os.path.isabs(resolved)


def test_resolve_container_path_inside_rootfs(builders):
    builders.make_container("box")
    resolved = paths.resolve_container_path("box:/etc/passwd")
    expected = os.path.join(paths.container_rootfs("box"), "etc", "passwd")
    assert resolved == os.path.normpath(expected)


def test_resolve_container_path_leading_slash_stripped(builders):
    builders.make_container("box")
    # Both forms land at the same place inside the rootfs.
    a = paths.resolve_container_path("box:/etc")
    b = paths.resolve_container_path("box:etc")
    assert a == b


@pytest.mark.parametrize("rel", ["../../etc/passwd", "../..", "a/../../../x"])
def test_resolve_container_path_rejects_escape(builders, capsys, rel):
    builders.make_container("box")
    with pytest.raises(SystemExit) as exc:
        paths.resolve_container_path(f"box:{rel}")
    assert exc.value.code == 1
    assert "escapes the container directory" in capsys.readouterr().err


def test_resolve_container_path_empty_name_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        paths.resolve_container_path(":/etc/passwd")
    assert exc.value.code == 1
    assert "invalid container name" in capsys.readouterr().err


def test_resolve_container_path_missing_container(capsys):
    with pytest.raises(SystemExit) as exc:
        paths.resolve_container_path("ghost:/etc")
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err


# ----- container_locks_for_spec_pair --------------------------------------

def _summarise(locks):
    return [(lk._display, lk._exclusive) for lk in locks]


def test_locks_same_container_single_exclusive():
    locks = paths.container_locks_for_spec_pair("box:/a", "box:/b", "copy")
    assert _summarise(locks) == [("box", True)]


def test_locks_two_containers_sorted_dst_exclusive():
    locks = paths.container_locks_for_spec_pair("src:/a", "dst:/b", "copy")
    # Sorted by name: dst, src. dst is exclusive, src shared.
    assert _summarise(locks) == [("dst", True), ("src", False)]


def test_locks_dst_only():
    locks = paths.container_locks_for_spec_pair("/host/path", "dst:/b", "copy")
    assert _summarise(locks) == [("dst", True)]


def test_locks_src_only():
    locks = paths.container_locks_for_spec_pair("src:/a", "/host/path", "copy")
    assert _summarise(locks) == [("src", False)]


def test_locks_neither():
    assert paths.container_locks_for_spec_pair("/a", "/b", "copy") == []
