# Integration tests for `command_install` from a local plain rootfs tarball.

import os
from types import SimpleNamespace

import pytest

from proot_distro.commands.install import command_install
from proot_distro.paths import (
    container_dir, container_manifest, container_rootfs,
)


def _args(image_ref, name=None, arch=None):
    return SimpleNamespace(
        image_ref=image_ref, custom_container_name=name, override_arch=arch,
    )


def test_install_plain_tar(tmp_path, builders):
    arc = tmp_path / "ubuntu-test.tar.gz"
    builders.make_tar(str(arc), builders.rootfs_members([
        {"name": "etc/os-release", "type": "file", "data": b"ID=test\n"},
    ]), compression="gz")

    command_install(_args(str(arc), name="mybox"))

    root = container_rootfs("mybox")
    assert os.path.isdir(root)
    assert open(os.path.join(root, "etc", "hostname"), "rb").read() == b"guest\n"
    # DNS + hosts fixups applied because /etc exists.
    assert "nameserver" in open(os.path.join(root, "etc", "resolv.conf")).read()
    assert os.path.isfile(os.path.join(root, "etc", "hosts"))
    # Plain tarballs carry no manifest.json.
    assert not os.path.exists(container_manifest("mybox"))
    # Fake sysdata stub directory created alongside the rootfs.
    assert os.path.isdir(os.path.join(container_dir("mybox"), "sysdata"))


def test_install_plain_tar_name_derived_from_filename(tmp_path, builders):
    arc = tmp_path / "fedora-rootfs.tar.gz"
    builders.make_tar(str(arc), builders.rootfs_members(), compression="gz")
    command_install(_args(str(arc)))  # no custom name
    assert os.path.isdir(container_rootfs("fedora-rootfs"))


def test_install_already_exists(tmp_path, builders, capsys):
    arc = tmp_path / "dup.tar.gz"
    builders.make_tar(str(arc), builders.rootfs_members(), compression="gz")
    command_install(_args(str(arc), name="dup"))
    capsys.readouterr()
    with pytest.raises(SystemExit) as exc:
        command_install(_args(str(arc), name="dup"))
    assert exc.value.code == 1
    assert "already exists" in capsys.readouterr().err


def test_install_strip_count_heuristic(tmp_path, builders):
    # A tarball wrapping the rootfs in a top-level directory must still land
    # etc/, usr/, ... at the rootfs root.
    members = [
        {"name": "fedora/", "type": "dir"},
        {"name": "fedora/etc/", "type": "dir"},
        {"name": "fedora/etc/hostname", "type": "file", "data": b"wrapped\n"},
        {"name": "fedora/usr/", "type": "dir"},
        {"name": "fedora/bin/", "type": "dir"},
    ]
    arc = tmp_path / "wrapped.tar.gz"
    builders.make_tar(str(arc), members, compression="gz")
    command_install(_args(str(arc), name="wrap"))
    root = container_rootfs("wrap")
    assert open(os.path.join(root, "etc", "hostname"), "rb").read() == b"wrapped\n"
    assert not os.path.exists(os.path.join(root, "fedora"))


def test_install_missing_local_file(tmp_path, capsys):
    with pytest.raises(SystemExit) as exc:
        command_install(_args(str(tmp_path / "nope.tar"), name="x"))
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err
