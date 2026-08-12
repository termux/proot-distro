# Unit tests for the programmatic interface (proot_distro.api), which runs
# commands inside containers and reinstalls them without going through the
# CLI. subprocess.run is stubbed so no real proot is ever spawned.

import json
import os
from types import SimpleNamespace

import pytest

from proot_distro import api
from proot_distro.api import (
    CommandError,
    ContainerNotInstalled,
    ProotDistroError,
    ProotResult,
    run,
    reinstall,
)
from proot_distro.arch import get_device_cpu_arch
from proot_distro.paths import container_manifest, container_rootfs


HOST_ARCH = get_device_cpu_arch()


class _FakeProc:
    def __init__(self, returncode=0, stdout=b"", stderr=b""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


@pytest.fixture
def fake_run(monkeypatch):
    """Replace subprocess.run with a recorder; return (calls, set_result)."""
    calls = []

    def set_result(returncode=0, stdout=b"", stderr=b""):
        nonlocal result
        result = _FakeProc(returncode, stdout, stderr)

    result = _FakeProc()
    monkeypatch.setattr(
        api.subprocess, "run",
        lambda *a, **kw: (calls.append((a, kw)) or result),
    )
    return calls, set_result


def test_run_returns_result_with_output(builders, fake_run):
    builders.make_container("box", arch=HOST_ARCH)
    _, set_result = fake_run
    set_result(0, b"hello\n", b"")

    res = run("box", ["sh", "-c", "echo hello"])

    assert isinstance(res, ProotResult)
    assert res.returncode == 0
    assert res.stdout == b"hello\n"
    assert res.ok


def test_run_spawns_proot_with_guest_argv(builders, fake_run):
    builders.make_container("box", arch=HOST_ARCH)
    calls, _ = fake_run

    run("box", ["echo", "hi"])

    (proot_args,), kw = calls[0]
    assert kw["env"] is not None
    assert kw["stdout"] == api.subprocess.PIPE
    assert proot_args[0] == "proot" or os.path.basename(proot_args[0]) == "proot"
    assert f"--rootfs={container_rootfs('box')}" in proot_args
    assert "--change-id=0:0" in proot_args
    assert proot_args[-2:] == ["echo", "hi"]
    # The child env is a fresh dict built like login's, not the host env.
    assert "PATH" in kw["env"]


def test_run_applies_user_bind_env_workdir(builders, fake_run):
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_config": {"config": {"Env": ["FROMIMAGE=yes"]}},
    })
    calls, _ = fake_run

    run("box", ["id"], user="tester", work_dir="/home/tester",
        env=["FOO=bar"], bind=["/data/foo:/mnt/foo"])

    (proot_args,), kw = calls[0]
    assert "--change-id=1000:1000" in proot_args
    assert "--cwd=/home/tester" in proot_args
    assert "--bind=/data/foo:/mnt/foo" in proot_args
    assert kw["env"]["FROMIMAGE"] == "yes"
    assert kw["env"]["FOO"] == "bar"
    assert kw["env"]["HOME"] == "/home/tester"


def test_run_check_raises_on_nonzero(builders, fake_run):
    builders.make_container("box", arch=HOST_ARCH)
    _, set_result = fake_run
    set_result(2, b"", b"boom")

    with pytest.raises(CommandError) as exc:
        run("box", ["false"], check=True)
    assert "boom" in str(exc.value)


def test_run_check_false_returns_nonzero(builders, fake_run):
    builders.make_container("box", arch=HOST_ARCH)
    _, set_result = fake_run
    set_result(7, b"", b"")

    res = run("box", ["false"])
    assert res.returncode == 7
    assert not res.ok


def test_run_rejects_string_argv(builders, fake_run):
    builders.make_container("box", arch=HOST_ARCH)
    with pytest.raises(TypeError):
        run("box", "echo hi")


def test_run_rejects_empty_argv(builders, fake_run):
    builders.make_container("box", arch=HOST_ARCH)
    with pytest.raises(ValueError):
        run("box", [])


def test_run_missing_container_raises(fake_run):
    with pytest.raises(ContainerNotInstalled):
        run("not-installed", ["echo", "hi"])


def test_run_invalid_name_raises(fake_run):
    with pytest.raises(SystemExit):
        run("bad name!", ["echo", "hi"])


def test_run_missing_proot_binary_raises(builders, monkeypatch):
    builders.make_container("box", arch=HOST_ARCH)

    def boom(*a, **kw):
        raise FileNotFoundError("no proot")

    monkeypatch.setattr(api.subprocess, "run", boom)
    with pytest.raises(ProotDistroError):
        run("box", ["echo", "hi"])


# ---------------------------------------------------------------------------
# reinstall
# ---------------------------------------------------------------------------

def _manifest_data(image_ref="test:latest", arch=None):
    return {"image_ref": image_ref, "arch": arch or HOST_ARCH,
            "manifest": {"schemaVersion": 2, "layers": []},
            "image_config": {"config": {}}}


def test_reinstall_removes_rootfs_and_installs(builders, monkeypatch, capsys):
    builders.make_container("box", arch=HOST_ARCH,
                            manifest=_manifest_data("ubuntu:24.04"),
                            files={"usr/bin/marker": b"old"})
    install_calls = []

    def fake_install(args):
        install_calls.append(args)
        os.makedirs(container_rootfs("box"), exist_ok=True)

    monkeypatch.setattr(api, "command_install", fake_install)

    assert reinstall("box") is None

    assert install_calls
    assert install_calls[0].image_ref == "ubuntu:24.04"
    assert install_calls[0].custom_container_name == "box"
    assert install_calls[0].override_arch == HOST_ARCH
    # The old rootfs content was wiped before reinstall.
    assert not os.path.exists(os.path.join(container_rootfs("box"),
                                           "usr", "bin", "marker"))
    # The manifest survives the reinstall.
    assert os.path.isfile(container_manifest("box"))


def test_reinstall_missing_container_raises():
    with pytest.raises(ContainerNotInstalled):
        reinstall("ghost")


def test_reinstall_without_manifest_raises(builders):
    builders.make_container("box", arch=HOST_ARCH, manifest=None)
    with pytest.raises(ProotDistroError) as exc:
        reinstall("box")
    assert "no OCI manifest" in str(exc.value)


def test_reinstall_bad_manifest_raises(builders):
    builders.make_container("box", arch=HOST_ARCH, manifest=None)
    with open(container_manifest("box"), "w") as fh:
        fh.write("{ not json")
    with pytest.raises(ProotDistroError):
        reinstall("box")


def test_reinstall_empty_image_ref_raises(builders):
    builders.make_container("box", arch=HOST_ARCH,
                            manifest=_manifest_data(image_ref=None))
    with pytest.raises(ProotDistroError):
        reinstall("box")


def test_build_login_runtime_shared_with_cli(builders):
    """The API resolves the same runtime dict the CLI login path uses."""
    from proot_distro.commands.login import build_login_runtime

    builders.make_container("box", arch=HOST_ARCH)
    args = SimpleNamespace(
        container_name="box", user="root", kernel=None, hostname=None,
        work_dir="", redirect_ports=False, isolated=False, minimal=False,
        shared_home=False, shared_tmp=False, shared_x11=False,
        no_link2symlink=False, no_sysvipc=False, no_kill_on_exit=False,
        bind=[], env=[], login_cmd=[], _run_inner=["/bin/echo", "x"],
        emulator=None,
    )
    rt = build_login_runtime("box", args)
    assert rt["inner"] == ["/bin/echo", "x"]
    assert rt["proot_args"][-2:] == ["/bin/echo", "x"]
    assert rt["rootfs"] == container_rootfs("box")
