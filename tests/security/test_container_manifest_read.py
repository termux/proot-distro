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


# --- what the fields inside it may be -------------------------------------
#
# The name is refused when it is not a plain file, but the file under it
# is still the container directory's, and that directory is guest-
# writable on Termux. So what the document *says* is a running session's
# to choose, and every consumer used to believe it: `.get` was called on
# whatever stood under `image_config`, and the reference was handed to
# str methods whatever its type. None of `login`, `run`, `reset`,
# `remove --image` or `list --image` catches an AttributeError.

@pytest.mark.parametrize("image_config", ["nope", [], 5, ["config"]])
def test_a_non_object_image_config_is_no_image_config(builders, image_config):
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": "real:1", "arch": HOST_ARCH,
        "image_config": image_config,
    })
    assert paths.container_image_config("box") == {}

    from proot_distro.commands.run import _read_image_config
    assert _read_image_config("box") == {}

    from proot_distro.commands.login.env import read_manifest_env
    assert read_manifest_env("box") == []


@pytest.mark.parametrize("config", ["nope", [], 5])
def test_a_non_object_config_is_no_config(builders, config):
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": "real:1", "arch": HOST_ARCH,
        "image_config": {"config": config},
    })
    assert paths.container_image_config("box") == {}


@pytest.mark.parametrize("payload,expected", [
    ({"image_ref": "real:1", "arch": HOST_ARCH}, ("real:1", HOST_ARCH)),
    ({"image_ref": 5, "arch": HOST_ARCH}, ("", HOST_ARCH)),
    ({"image_ref": "real:1", "arch": 5}, ("real:1", "")),
    ({"image_ref": {}, "arch": []}, ("", "")),
    # A field that is simply absent has always answered the same way as
    # one this refuses, and each caller already handles it: `reset`
    # reinstalls without an architecture override, and the cache's
    # reference hints skip the container.
    ({"image_ref": "real:1"}, ("real:1", "")),
    ({"arch": HOST_ARCH}, ("", HOST_ARCH)),
    ({}, ("", "")),
])
def test_container_image_origin_answers_with_strings(builders, payload,
                                                     expected):
    builders.make_container("box", arch=HOST_ARCH, manifest=payload)
    assert paths.container_image_origin("box") == expected


def test_container_image_origin_of_an_unreadable_manifest(planted):
    assert paths.container_image_origin("box") == ("", "")


def test_reset_refuses_a_reference_that_is_not_a_string(builders, capsys):
    # It used to reach install(), which asks the reference whether it
    # startswith('/') -- after reset had already deleted the rootfs.
    from proot_distro.commands.reset import command_reset
    from proot_distro.paths import container_rootfs

    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": 5, "arch": HOST_ARCH,
    })
    with pytest.raises(SystemExit) as exc:
        command_reset(SimpleNamespace(container_name="box"))
    assert exc.value.code == 1
    assert "no OCI" in capsys.readouterr().err
    # Refused before anything was removed.
    assert os.path.isdir(container_rootfs("box"))


def test_remove_image_survives_a_container_naming_a_bad_reference(builders):
    # Every installed container is walked to report which were installed
    # from the image being removed, so one bad manifest.json ended the
    # command for an image it has nothing to do with.
    from proot_distro.commands.remove import command_remove
    from proot_distro.helpers.docker.cache import (
        manifest_cache_path, save_manifest_cache,
    )

    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": 5, "arch": HOST_ARCH,
    })
    save_manifest_cache("img:1", HOST_ARCH, {"layers": []}, "library/img", {})

    command_remove(SimpleNamespace(target="img:1", image=True, verbose=False,
                                   override_arch=None))
    assert not os.path.exists(manifest_cache_path("img:1", HOST_ARCH))


def test_list_image_survives_a_container_naming_a_bad_reference(builders,
                                                               capsys):
    # The same walk, reached through the cache's reference hints: an
    # entry with no image_ref of its own is named from installed
    # containers.
    from proot_distro.commands.list import command_list
    from proot_distro.constants import MANIFEST_CACHE_DIR
    from proot_distro.helpers.docker.cache import manifest_cache_name

    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": 5, "arch": HOST_ARCH,
    })
    name = manifest_cache_name("img:1", HOST_ARCH)
    os.makedirs(MANIFEST_CACHE_DIR, exist_ok=True)
    with open(os.path.join(MANIFEST_CACHE_DIR, name), "w") as fh:
        json.dump({"manifest": {"layers": []}}, fh)

    command_list(SimpleNamespace(image=True, quiet=True))
    assert capsys.readouterr().out.strip()


def test_a_real_manifest_is_still_read(builders):
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": "real:1", "arch": HOST_ARCH,
        "image_config": {"config": {"Env": ["REAL=yes"], "Cmd": ["/bin/sh"]}},
    })
    assert paths.read_container_manifest("box")["image_ref"] == "real:1"
    assert paths.container_image_config("box")["Env"] == ["REAL=yes"]
