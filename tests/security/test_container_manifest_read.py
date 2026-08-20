# Containment tests for containers/<name>/manifest.json, the sentinel
# `login` takes the image's Env from and `run` takes its Entrypoint/Cmd
# from.
#
# It was opened by its composed path. The container directory is
# guest-writable on Termux -- it sits under the $TERMUX_PREFIX bound
# read-write into every non-isolated container -- so a session could
# leave the name behind as a symlink and have the image config come out
# of any file the user can read, or as a FIFO, where the open waits for
# a peer that never arrives and the command hangs for as long as the
# user leaves it running.

import json
import os
from types import SimpleNamespace

import pytest

from proot_distro import paths
from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.login import command_login
from proot_distro.commands.run import command_run
from proot_distro.paths import container_manifest


HOST_ARCH = get_device_cpu_arch()

_PLANTED = {
    "image_ref": "planted:1", "arch": HOST_ARCH,
    "image_config": {"config": {
        "Env": ["INJECTED=1"], "Cmd": ["/bin/planted"],
    }},
}


def _login_args(name, **over):
    base = dict(container_name=name, get_proot_cmd=True, user="root",
                kernel=None, hostname="localhost", work_dir="",
                redirect_ports=False, isolated=False, minimal=False,
                shared_home=False, shared_tmp=False, shared_x11=False,
                no_link2symlink=False, no_sysvipc=False,
                no_kill_on_exit=False, bind=[], env=[], login_cmd=[],
                emulator=None, detach=False)
    base.update(over)
    return SimpleNamespace(**base)


@pytest.fixture
def planted(tmp_path, builders):
    """A container whose manifest.json is a link to a file outside it."""
    builders.make_container("box", arch=HOST_ARCH)
    outside = tmp_path / "outside.json"
    outside.write_text(json.dumps(_PLANTED))
    path = container_manifest("box")
    if os.path.lexists(path):
        os.unlink(path)
    os.symlink(str(outside), path)
    return outside


@pytest.fixture
def fifo(builders):
    """A container whose manifest.json is a FIFO with no writer."""
    builders.make_container("box", arch=HOST_ARCH)
    path = container_manifest("box")
    if os.path.lexists(path):
        os.unlink(path)
    os.mkfifo(path)
    return path


def test_a_symlinked_manifest_is_not_read(planted):
    with pytest.raises(OSError):
        paths.read_container_manifest("box")
    assert paths.container_image_config("box") == {}


def test_login_env_does_not_come_from_a_symlinked_manifest(planted, capsys):
    with pytest.raises(SystemExit) as exc:
        command_login(_login_args("box"))
    assert exc.value.code == 0
    assert "INJECTED" not in capsys.readouterr().out

    # The same values in a real manifest do reach the session, so the
    # assertion above is about where they came from, not about Env.
    os.unlink(container_manifest("box"))
    with open(container_manifest("box"), "w") as fh:
        json.dump(_PLANTED, fh)
    with pytest.raises(SystemExit):
        command_login(_login_args("box"))
    assert "INJECTED=1" in capsys.readouterr().out


def test_run_refuses_a_symlinked_manifest(planted, capsys):
    with pytest.raises(SystemExit) as exc:
        command_run(SimpleNamespace(
            container_name="box", run_args=[], get_proot_cmd=True,
            **{k: v for k, v in vars(_login_args("box")).items()
               if k not in ("container_name", "get_proot_cmd")},
        ))
    assert exc.value.code == 1
    assert "/bin/planted" not in capsys.readouterr().out


def test_a_fifo_manifest_does_not_block(fifo):
    # The open would wait for a writer that never comes; open_regular_at
    # returns instead and the fstat refuses the type.
    with pytest.raises(OSError):
        paths.read_container_manifest("box")
    assert paths.container_image_config("box") == {}


def test_a_real_manifest_is_still_read(builders):
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": "real:1", "arch": HOST_ARCH,
        "image_config": {"config": {"Env": ["REAL=yes"], "Cmd": ["/bin/sh"]}},
    })
    assert paths.read_container_manifest("box")["image_ref"] == "real:1"
    assert paths.container_image_config("box")["Env"] == ["REAL=yes"]
