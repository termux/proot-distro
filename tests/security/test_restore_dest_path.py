# Containment tests for proot_distro.commands.restore — the archive-member ->
# on-disk path mapper and the full restore of a hostile archive.

import os

import pytest

from proot_distro.commands import restore
from proot_distro.constants import CONTAINERS_DIR
from proot_distro.paths import (
    container_dir, container_manifest, container_rootfs,
)


def test_new_format_rootfs_member():
    name, dest = restore._dest_path("ubuntu/rootfs/etc/hostname")
    assert name == "ubuntu"
    assert dest == os.path.join(container_rootfs("ubuntu"), "etc", "hostname")


def test_new_format_manifest_member():
    name, dest = restore._dest_path("ubuntu/manifest.json")
    assert name == "ubuntu"
    assert dest == container_manifest("ubuntu")


def test_new_format_rootfs_root():
    name, dest = restore._dest_path("ubuntu/rootfs")
    assert name == "ubuntu"
    assert dest == container_rootfs("ubuntu")


def test_legacy_format_rerooted():
    name, dest = restore._dest_path("installed-rootfs/ubuntu/etc/x")
    assert name == "ubuntu"
    assert dest == os.path.join(container_rootfs("ubuntu"), "etc", "x")


@pytest.mark.parametrize("member", [
    "../evil/x",
    "ubuntu/../../etc/passwd",
    "a/../../b",
    "./",
    "",
])
def test_traversal_and_empty_skipped(member):
    assert restore._dest_path(member) == (None, None)


@pytest.mark.parametrize("member", [
    "foo bar/rootfs/x",      # invalid container name (space)
    "../x/rootfs/y",         # leading .. component
    "..",                    # bare dotdot
])
def test_invalid_container_name_skipped(member):
    assert restore._dest_path(member) == (None, None)


def test_bare_single_component_skipped():
    # A single component with no trailing slash is not a real subdir.
    assert restore._dest_path("justafile") == (None, None)


def test_leading_slash_stays_within_containers_dir():
    # Absolute-looking members are re-rooted under containers/, never escape.
    name, dest = restore._dest_path("/abs/path")
    assert name == "abs"
    assert os.path.abspath(dest).startswith(os.path.abspath(CONTAINERS_DIR) + os.sep)


# ----- full restore of a hostile archive ----------------------------------

def _run_restore(tmp_path, members):
    from _builders import make_tar
    arc = tmp_path / "backup.tar"
    make_tar(str(arc), members)
    args = type("A", (), {"archive": str(arc), "verbose": False})()
    restore.command_restore(args)


def test_restore_hostile_archive_contained(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "secret"
    sentinel.write_text("SECRET")

    _run_restore(tmp_path, [
        {"name": "../escape", "type": "file", "data": b"P"},
        {"name": "box/rootfs/etc/hostname", "type": "file", "data": b"guest"},
        {"name": "box/rootfs/bad", "type": "hardlink",
         "linkname": "../../../../etc/shadow"},
        # Absolute-looking member must be re-rooted under the same container,
        # never escape onto the host. (A second container name would be
        # rejected outright — see test_restore_multiple_containers_rejected.)
        {"name": "/box/evil", "type": "file", "data": b"P"},
    ])

    # The good container was created inside the sandbox.
    assert os.path.exists(
        os.path.join(container_rootfs("box"), "etc", "hostname")
    )
    # The hostile hard link was not materialised from the host.
    assert not os.path.exists(os.path.join(container_rootfs("box"), "bad"))
    # The absolute member landed inside the container, not on the host.
    assert os.path.exists(os.path.join(container_rootfs("box"), "evil"))
    # Nothing escaped to the host.
    assert sentinel.read_text() == "SECRET"
    assert not os.path.exists(os.path.join(os.path.dirname(str(tmp_path)), "escape"))


def test_restore_multiple_containers_rejected(tmp_path):
    # An archive holding members for two distinct containers must be
    # refused: restore handles a single container at a time.
    with pytest.raises(SystemExit) as exc:
        _run_restore(tmp_path, [
            {"name": "box/rootfs/etc/hostname", "type": "file", "data": b"a"},
            {"name": "other/rootfs/etc/hostname", "type": "file", "data": b"b"},
        ])
    assert exc.value.code == 1
    # The second container was never created.
    assert not os.path.exists(container_dir("other"))


def test_restore_bare_root_archive_rejected(tmp_path):
    from _builders import make_tar
    arc = tmp_path / "bad.tar"
    make_tar(str(arc), [{"name": "loosefile", "type": "file", "data": b"x"}])
    args = type("A", (), {"archive": str(arc), "verbose": False})()
    with pytest.raises(SystemExit) as exc:
        restore.command_restore(args)
    assert exc.value.code == 1
