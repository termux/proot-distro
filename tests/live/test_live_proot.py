# Opt-in live test: real proot execution end-to-end. Skipped unless
# RUN_LIVE_TESTS=1, and additionally skipped when proot is not installed.
#
#   RUN_LIVE_TESTS=1 python -m pytest tests/live/test_live_proot.py -q

import os
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

from proot_distro import prootcheck
from proot_distro.arch import get_proot_bin
from proot_distro.commands.install import command_install
from proot_distro.commands.remove import command_remove
from proot_distro.paths import container_rootfs

pytestmark = pytest.mark.live


@pytest.mark.skipif(shutil.which("proot") is None, reason="proot not installed")
def test_live_proot_run_echo():
    name = "live-proot"
    try:
        command_install(SimpleNamespace(
            image_ref="alpine:latest", custom_container_name=name,
            override_arch=None,
        ))
        assert os.path.isdir(container_rootfs(name))

        # `login` execs proot (replacing the process), so run it in a child.
        # The child inherits this process's sandbox XDG_* env, so it resolves
        # the same container store. Reading the guest's os-release proves
        # the session is in the container: a proot that skips translation
        # (Ubuntu 26.04's 5.4.0 in seccomp mode) runs the host's echo and
        # cat against the host's files.
        code = (
            "import sys;"
            "from proot_distro.cli import main;"
            f"sys.argv=['pd','login','{name}','--','sh','-c',"
            "'echo PROOT_OK; cat /etc/os-release'];"
            "main()"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True, env=os.environ.copy(), timeout=120,
        )
        assert "PROOT_OK" in result.stdout, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
        assert "ID=alpine" in result.stdout, (
            f"stdout={result.stdout!r} stderr={result.stderr!r}"
        )
    finally:
        if os.path.isdir(container_rootfs(name)):
            command_remove(SimpleNamespace(target=name, verbose=False))


@pytest.mark.skipif(shutil.which("proot") is None, reason="proot not installed")
def test_live_proot_self_test_finds_a_confining_mode(real_proot_probe):
    # Whatever confining_env() settles on for the installed proot, the
    # self-test must then see the bind through it.
    proot = get_proot_bin()
    extra = prootcheck.confining_env(proot, {})

    assert prootcheck._probe(proot, extra) is True
