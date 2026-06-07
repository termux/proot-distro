# Integration tests for `command_install` from a local OCI image-layout
# tarball.

import json
import os
from types import SimpleNamespace

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.install import command_install
from proot_distro.paths import container_manifest, container_rootfs


def _args(image_ref, name=None, arch=None):
    return SimpleNamespace(
        image_ref=image_ref, custom_container_name=name, override_arch=arch,
    )


def test_install_oci_archive(tmp_path, builders):
    arch = get_device_cpu_arch()
    arc = tmp_path / "image.oci.tar"
    builders.make_oci_archive(
        str(arc),
        [[
            {"name": "etc/hostname", "type": "file", "data": b"oci-guest\n"},
            {"name": "etc/os-release", "type": "file", "data": b"ID=oci\n"},
        ]],
        arch=arch,
        image_ref="myimg:1.0",
    )

    command_install(_args(str(arc), name="ocibox", arch=arch))

    root = container_rootfs("ocibox")
    assert open(os.path.join(root, "etc", "hostname"), "rb").read() == b"oci-guest\n"

    # OCI installs write manifest.json with the embedded ref + arch.
    with open(container_manifest("ocibox")) as fh:
        meta = json.load(fh)
    assert meta["image_ref"] == "myimg:1.0"
    assert meta["arch"] == arch


def test_install_oci_multi_layer_overlay(tmp_path, builders):
    arch = get_device_cpu_arch()
    arc = tmp_path / "multi.oci.tar"
    builders.make_oci_archive(
        str(arc),
        [
            [{"name": "etc/a", "type": "file", "data": b"base\n"}],
            [{"name": "etc/b", "type": "file", "data": b"top\n"},
             {"name": "etc/a", "type": "file", "data": b"overlaid\n"}],
        ],
        arch=arch,
    )
    command_install(_args(str(arc), name="multi", arch=arch))
    root = container_rootfs("multi")
    # Upper layer wins for etc/a; etc/b present from the upper layer.
    assert open(os.path.join(root, "etc", "a"), "rb").read() == b"overlaid\n"
    assert open(os.path.join(root, "etc", "b"), "rb").read() == b"top\n"
