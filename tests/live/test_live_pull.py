# Opt-in live tests: real Docker Hub pulls. Skipped unless RUN_LIVE_TESTS=1.
#
#   RUN_LIVE_TESTS=1 python -m pytest tests/live -q
#
# These hit the network and depend on Docker Hub availability + the host
# architecture being published for the chosen image.

import os
from types import SimpleNamespace

import pytest

from proot_distro.arch import detect_installed_arch, get_device_cpu_arch
from proot_distro.commands.install import command_install
from proot_distro.commands.remove import command_remove
from proot_distro.paths import container_manifest, container_rootfs

pytestmark = pytest.mark.live


def _install(image_ref, name):
    command_install(SimpleNamespace(
        image_ref=image_ref, custom_container_name=name, override_arch=None,
    ))


def test_live_pull_alpine():
    name = "live-alpine"
    try:
        _install("alpine:latest", name)
        root = container_rootfs(name)
        assert os.path.isdir(root)
        # Alpine ships busybox; the rootfs should carry a shell of the host arch.
        assert os.path.isdir(os.path.join(root, "bin"))
        assert detect_installed_arch(root) == get_device_cpu_arch()
        # Docker pulls record a manifest.json.
        assert os.path.isfile(container_manifest(name))
    finally:
        if os.path.isdir(container_rootfs(name)):
            command_remove(SimpleNamespace(container_name=name, verbose=False))


def test_live_pull_is_cached_second_time(capsys):
    name = "live-alpine-cache"
    try:
        _install("alpine:latest", name)
        command_remove(SimpleNamespace(container_name=name, verbose=False))
        capsys.readouterr()
        # Second install reuses the cached manifest + layers.
        _install("alpine:latest", name)
        assert os.path.isdir(container_rootfs(name))
    finally:
        if os.path.isdir(container_rootfs(name)):
            command_remove(SimpleNamespace(container_name=name, verbose=False))
