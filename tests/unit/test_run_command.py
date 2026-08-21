# Tests for proot_distro.commands.run — the image config fields `run`
# turns into an argv.
#
# containers/<name>/manifest.json holds a registry's JSON verbatim, and
# on Termux the file sits under the $TERMUX_PREFIX bound read-write into
# every non-isolated container. `list(cfg.get("Entrypoint") or [])`
# believed whatever shape it found there: an int ended the command in a
# TypeError traceback, an object yielded its keys, and "sh" became
# ['s', 'h'].

from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.run import command_run


HOST_ARCH = get_device_cpu_arch()


def _args(name="box", **over):
    base = dict(container_name=name, run_args=[], get_proot_cmd=True,
                user="root", kernel=None, hostname="localhost",
                work_dir="", redirect_ports=False, isolated=False,
                minimal=False, shared_home=False, shared_tmp=False,
                shared_x11=False, no_link2symlink=False, no_sysvipc=False,
                no_kill_on_exit=False, bind=[], env=[], login_cmd=[],
                emulator=None, detach=False)
    base.update(over)
    return SimpleNamespace(**base)


def _container(builders, name="box", **config):
    """A container whose image config holds exactly *config*."""
    builders.make_container("box" if name is None else name, arch=HOST_ARCH,
                            manifest={
                                "image_ref": "x:1", "arch": HOST_ARCH,
                                "manifest": {"schemaVersion": 2, "layers": []},
                                "image_config": {"config": dict(config)},
                            })


def _run(capsys, **over):
    """Run `run` against container 'box'; return (exit code, stdout, stderr)."""
    with pytest.raises(SystemExit) as exc:
        command_run(_args(**over))
    out = capsys.readouterr()
    return exc.value.code, out.out, out.err


# --- the shapes that used to get through ----------------------------------

@pytest.mark.parametrize("key", ["Entrypoint", "Cmd"])
@pytest.mark.parametrize("value", [
    5,                       # TypeError out of list()
    "sh",                    # became ['s', 'h']
    {"a": 1},                # became its keys
    ["/bin/sh", 7],          # TypeError out of os.execvpe(), past every net
    [["/bin/sh"]],
    True,
])
def test_a_malformed_command_field_is_refused(builders, capsys, key, value):
    _container(builders, **{key: value})
    code, out, err = _run(capsys)
    assert code == 1
    assert f"{key} that is not a list of strings" in err
    assert "Traceback" not in err
    # Nothing was handed to proot.
    assert "--rootfs" not in out


@pytest.mark.parametrize("value", [5, ["/opt"], {"a": 1}, True])
def test_a_malformed_workingdir_is_refused(builders, capsys, value):
    _container(builders, Cmd=["/bin/sh"], WorkingDir=value)
    code, _out, err = _run(capsys)
    assert code == 1
    assert "WorkingDir that is not a string" in err


# --- what still has to work ------------------------------------------------

def test_a_wellformed_config_still_runs(builders, capsys):
    _container(builders, Entrypoint=["/bin/echo"], Cmd=["hi"],
               WorkingDir="/srv")
    code, out, _err = _run(capsys)
    assert code == 0
    assert "/bin/echo" in out and "hi" in out
    assert "--cwd=/srv" in out


def test_absent_fields_are_not_malformed(builders, capsys):
    # install writes no Entrypoint/Cmd/WorkingDir key at all when the
    # image has none, and a registry may write JSON null for them.
    _container(builders, Cmd=["/bin/sh"])
    code, out, _err = _run(capsys)
    assert code == 0
    assert "--cwd=/" in out

    builders.make_container("box2", arch=HOST_ARCH, manifest={
        "image_ref": "x:1", "arch": HOST_ARCH,
        "image_config": {"config": {
            "Entrypoint": None, "Cmd": ["/bin/sh"], "WorkingDir": None,
        }},
    })
    with pytest.raises(SystemExit) as exc:
        command_run(_args("box2"))
    assert exc.value.code == 0
    assert "--cwd=/" in capsys.readouterr().out


def test_an_empty_string_argument_is_a_valid_argv_entry(builders, capsys):
    _container(builders, Entrypoint=["/bin/echo"], Cmd=[""])
    code, _out, _err = _run(capsys)
    assert code == 0


def test_an_empty_workingdir_falls_back_to_root(builders, capsys):
    _container(builders, Cmd=["/bin/sh"], WorkingDir="")
    code, out, _err = _run(capsys)
    assert code == 0
    assert "--cwd=/" in out
