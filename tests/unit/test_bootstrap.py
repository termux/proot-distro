import os
import sys

import pytest

from proot_distro import _bootstrap
from proot_distro._bootstrap import private_module_command


def test_private_module_command_isolated_and_uses_imported_package():
    command = private_module_command("proot_distro.api._worker")

    assert command[0] == sys.executable
    assert command[1:3] == ["-I", "-S"]
    assert command[-1] == "proot_distro.api._worker"
    assert os.path.isabs(command[-2])
    assert command[-2] == os.path.dirname(os.path.dirname(
        os.path.abspath(_bootstrap.__file__)
    ))


@pytest.mark.parametrize("module", [None, "", "json", 1])
def test_private_module_command_rejects_non_private_module(module):
    with pytest.raises(ValueError):
        private_module_command(module)
