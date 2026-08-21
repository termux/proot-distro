# Containment tests for proot_distro.commands.restore — the archive-member ->
# on-disk path mapper and the full restore of a hostile archive.

import os

import pytest

from proot_distro.commands import restore
from proot_distro.constants import CONTAINERS_DIR
from proot_distro.paths import container_dir, container_rootfs


# The mapper answers in components *below* containers/<name>, which is
# the descriptor the extraction descends from — never a composed path.

def test_new_format_rootfs_member():
    name, parts = restore._dest_path("ubuntu/rootfs/etc/hostname")
    assert name == "ubuntu"
    assert parts == ("rootfs", "etc", "hostname")


def test_new_format_manifest_member():
    name, parts = restore._dest_path("ubuntu/manifest.json")
    assert name == "ubuntu"
    assert parts == ("manifest.json",)


def test_new_format_rootfs_root():
    name, parts = restore._dest_path("ubuntu/rootfs")
    assert name == "ubuntu"
    assert parts == ("rootfs",)


def test_legacy_format_rerooted():
    name, parts = restore._dest_path("installed-rootfs/ubuntu/etc/x")
    assert name == "ubuntu"
    assert parts == ("rootfs", "etc", "x")


def test_old_layout_member_lands_in_the_rootfs():
    name, parts = restore._dest_path("ubuntu/etc/x")
    assert name == "ubuntu"
    assert parts == ("rootfs", "etc", "x")


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
    name, parts = restore._dest_path("/abs/path")
    assert name == "abs"
    assert parts == ("rootfs", "path")
    joined = os.path.join(container_dir(name), *parts)
    assert os.path.abspath(joined).startswith(
        os.path.abspath(CONTAINERS_DIR) + os.sep)


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


def test_restore_oversized_manifest_rejected(tmp_path, monkeypatch):
    # The archive is a stranger's file and its member sizes are its own
    # choice; the manifest is the one member read whole into memory.
    monkeypatch.setattr(restore, "_MAX_MANIFEST_BYTES", 64)
    with pytest.raises(SystemExit) as exc:
        _run_restore(tmp_path, [
            {"name": "box/manifest.json", "type": "file",
             "data": b"{}" + b" " * 128},
            {"name": "box/rootfs/etc/hostname", "type": "file", "data": b"a"},
        ])
    assert exc.value.code == 1
    # Refused before the destructive commit point: nothing was created.
    assert not os.path.exists(container_dir("box"))


def test_restore_manifest_at_the_limit_is_kept(tmp_path, monkeypatch):
    monkeypatch.setattr(restore, "_MAX_MANIFEST_BYTES", 64)
    payload = b"{}" + b" " * 62          # exactly the limit
    _run_restore(tmp_path, [
        {"name": "box/manifest.json", "type": "file", "data": payload},
        {"name": "box/rootfs/etc/hostname", "type": "file", "data": b"a"},
    ])
    with open(os.path.join(container_dir("box"), "manifest.json"), "rb") as fh:
        assert fh.read() == payload


def test_restore_manifest_only_rejected(tmp_path):
    # An archive that carries a manifest but no rootfs is not a usable
    # backup: it must be refused and must not create a phantom container.
    with pytest.raises(SystemExit) as exc:
        _run_restore(tmp_path, [
            {"name": "box/manifest.json", "type": "file", "data": b"{}"},
        ])
    assert exc.value.code == 1
    assert not os.path.exists(container_dir("box"))


def test_restore_empty_archive_rejected(tmp_path):
    # An archive with no usable members carries no rootfs — reject it
    # rather than reporting a bogus success.
    with pytest.raises(SystemExit) as exc:
        _run_restore(tmp_path, [])
    assert exc.value.code == 1


def test_restore_rootfs_as_file_rejected(tmp_path):
    # A member that drops a plain file where the rootfs directory should be
    # does not yield a usable container. The broken result must be removed,
    # not reported as a successful restore.
    with pytest.raises(SystemExit) as exc:
        _run_restore(tmp_path, [
            {"name": "box/manifest.json", "type": "file", "data": b"{}"},
            {"name": "box/rootfs", "type": "file", "data": b"NOTADIR"},
        ])
    assert exc.value.code == 1
    assert not os.path.exists(container_dir("box"))


def test_restore_rootfs_as_symlink_rejected(tmp_path):
    # A symlink standing in where the rootfs directory should be is not a
    # valid rootfs (and would escape the container); reject and clean up.
    with pytest.raises(SystemExit) as exc:
        _run_restore(tmp_path, [
            {"name": "box/manifest.json", "type": "file", "data": b"{}"},
            {"name": "box/rootfs", "type": "symlink", "linkname": "/etc"},
        ])
    assert exc.value.code == 1
    assert not os.path.exists(container_dir("box"))


def test_restore_will_not_write_through_a_planted_container_dir(tmp_path):
    # containers/<name> is guest-writable on Termux. _safe_dest clamped
    # every member "inside the container directory", which is exactly
    # where a planted link led, and the rootfs check ran after the writes.
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keepsake").write_text("host content\n")
    os.symlink(str(outside), container_dir("box"))

    with pytest.raises(SystemExit) as exc:
        _run_restore(tmp_path, [
            {"name": "box/manifest.json", "type": "file", "data": b"{}"},
            {"name": "box/rootfs/etc/hostname", "type": "file", "data": b"g"},
        ])
    assert exc.value.code == 1
    assert sorted(os.listdir(str(outside))) == ["keepsake"]


def test_restore_reanchors_a_symlink_the_archive_shipped(tmp_path):
    # An archive can ship `rootfs/etc -> <host dir>` and then a member
    # underneath it. The link is followed -- that is what a rootfs looks
    # like -- but re-anchored at the container directory, so the member
    # lands inside the container.
    outside = tmp_path / "outside"
    outside.mkdir()

    _run_restore(tmp_path, [
        {"name": "box/rootfs/keep", "type": "file", "data": b"real"},
        {"name": "box/rootfs/etc", "type": "symlink", "linkname": str(outside)},
        {"name": "box/rootfs/etc/passwd", "type": "file", "data": b"stolen"},
    ])

    assert os.listdir(str(outside)) == []
    # Re-anchored at the container root: <rootfs>/<outside-as-relative>.
    landed = os.path.join(
        container_rootfs("box"), str(outside).lstrip(os.sep), "passwd")
    assert open(landed, "rb").read() == b"stolen"
    # The link itself is restored verbatim, as a link — it is just never
    # written *through*.
    assert os.readlink(os.path.join(container_rootfs("box"), "etc")) == \
        str(outside)


def test_restore_replaces_a_symlink_standing_at_a_member_name(tmp_path):
    # The final component is never followed: a member replaces the entry
    # itself, so an earlier `x -> <host file>` does not turn the next
    # member into a write through it.
    victim = tmp_path / "victim"
    victim.write_text("host content\n")

    _run_restore(tmp_path, [
        {"name": "box/rootfs/x", "type": "symlink", "linkname": str(victim)},
        {"name": "box/rootfs/x", "type": "file", "data": b"member"},
    ])

    assert victim.read_text() == "host content\n"
    dest = os.path.join(container_rootfs("box"), "x")
    assert not os.path.islink(dest)
    assert open(dest, "rb").read() == b"member"


def test_restore_dangling_rootfs_member_rejected(tmp_path):
    # The only rootfs entry is a hardlink that resolves nowhere: nothing is
    # materialised, so the restore is rejected and no container is created.
    with pytest.raises(SystemExit) as exc:
        _run_restore(tmp_path, [
            {"name": "box/manifest.json", "type": "file", "data": b"{}"},
            {"name": "box/rootfs/x", "type": "hardlink",
             "linkname": "../../../../etc/shadow"},
        ])
    assert exc.value.code == 1
    assert not os.path.exists(container_dir("box"))
