import json
import os
import subprocess
import sys

import pytest

from proot_distro import _bootstrap
from proot_distro._bootstrap import private_module_command


def test_private_module_command_isolated_and_uses_imported_package():
    command = private_module_command("proot_distro._api_worker")

    assert command[0] == sys.executable
    assert command[1:3] == ["-I", "-S"]
    assert command[-1] == "proot_distro._api_worker"
    assert os.path.isabs(command[-2])
    assert command[-2] == os.path.dirname(os.path.dirname(
        os.path.abspath(_bootstrap.__file__)
    ))


@pytest.mark.parametrize("module", [None, "", "json", 1])
def test_private_module_command_rejects_non_private_module(module):
    with pytest.raises(ValueError):
        private_module_command(module)


def test_files_beside_the_package_do_not_shadow_the_standard_library(
        tmp_path):
    # The package's parent is site-packages or an application's vendor
    # directory. Prepending it let any `json.py` or `selectors.py` there
    # replace the standard library inside every API worker.
    parent = tmp_path / "vendor"
    parent.mkdir()
    real_parent = private_module_command("proot_distro._api_worker")[-2]
    (parent / "proot_distro").symlink_to(
        os.path.join(real_parent, "proot_distro")
    )
    for name in ("json", "selectors"):
        (parent / f"{name}.py").write_text(
            "import sys\nsys.stdout.write('SHADOWED')\nraise SystemExit(99)\n"
        )
    command = private_module_command("proot_distro._api_worker")
    command[-2] = str(parent)

    result = subprocess.run(
        command, input=b'{"operation":"list_containers","options":{}}',
        capture_output=True, timeout=60,
    )

    assert b"SHADOWED" not in result.stdout + result.stderr
    assert result.returncode != 99
    # The package itself still came from that directory: under -I -S
    # nothing else on the path holds one.
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["items"] == []
