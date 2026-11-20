# `reset` reinstalls a container from the image reference recorded in its
# manifest.json. On Termux that file is guest-writable: the runtime tree is
# bound into non-isolated containers. The reference was handed straight to
# the install service, which also installs from a local path or a URL, so
# a guest could write `/path/to/some/host/archive.tar` (or an http:// URL)
# there and have the next reset extract that host file -- someone else's
# backup, a private rootfs -- into its own rootfs, where it can read it.
#
# Each test plants such a reference and checks that reset refuses it before
# anything is removed or read, through the CLI and through the Python API.

import os
from types import SimpleNamespace

import pytest

from proot_distro.api import ProotDistro, UnsupportedOperationError
from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.reset import command_reset
from proot_distro.paths import container_rootfs

HOST_ARCH = get_device_cpu_arch()
SECRET = b"host-only secret\n"


@pytest.fixture
def host_archive(tmp_path, builders):
    """A rootfs tarball on the host that no container should see."""
    path = tmp_path / "host-backup.tar"
    builders.make_tar(str(path), builders.rootfs_members([
        {"name": "secret.txt", "type": "file", "data": SECRET},
    ]))
    return str(path)


def _planted(builders, image_ref):
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": image_ref, "arch": HOST_ARCH,
    })
    marker = os.path.join(container_rootfs("box"), "still-here")
    with open(marker, "wb") as fh:
        fh.write(b"x")
    return marker


def _references(host_archive):
    return [
        host_archive,
        "./" + os.path.relpath(host_archive),
        "file://" + host_archive,
        "http://127.0.0.1:9/rootfs.tar",
        "https://example.invalid/rootfs.tar.gz",
    ]


@pytest.mark.parametrize("index", range(5))
def test_cli_reset_refuses_a_planted_host_path_or_url(
        builders, host_archive, capsys, index):
    marker = _planted(builders, _references(host_archive)[index])

    with pytest.raises(SystemExit) as exc:
        command_reset(SimpleNamespace(container_name="box"))

    assert exc.value.code == 1
    assert "registry" in capsys.readouterr().err
    # Refused before the rootfs was cleared, and nothing was extracted.
    assert os.path.exists(marker)
    assert not os.path.exists(
        os.path.join(container_rootfs("box"), "secret.txt")
    )


@pytest.mark.parametrize("index", [0, 3])
def test_api_reset_refuses_a_planted_host_path_or_url(
        builders, host_archive, index):
    marker = _planted(builders, _references(host_archive)[index])

    with pytest.raises(UnsupportedOperationError) as exc:
        ProotDistro().reset_container("box")

    assert exc.value.operation == "reset_container"
    assert os.path.exists(marker)
    assert not os.path.exists(
        os.path.join(container_rootfs("box"), "secret.txt")
    )
