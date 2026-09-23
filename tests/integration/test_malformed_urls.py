# Integration tests: a URL or image reference urllib cannot use is a
# message from `install` and `build`, never a traceback.
#
# urllib answers `http://[` and the registry of `[:5000/img` with a bare
# ValueError out of the request's construction, and neither command's net
# caught one: install re-raised it past its cleanup, build's FROM caught
# only RuntimeError and ADD only the network family. No network is
# touched -- every one of these fails before a request can be sent.

import io
import os
import sys
from types import SimpleNamespace

import pytest

from proot_distro.commands import install as install_mod
from proot_distro.commands.build import command_build
from proot_distro.commands.install import command_install
from proot_distro.paths import container_dir


def _install_args(image_ref, name):
    return SimpleNamespace(
        image_ref=image_ref, custom_container_name=name, override_arch=None,
    )


def _build_args(ctx):
    return SimpleNamespace(
        path=str(ctx), dockerfile=None, tags=["badurl:1"], build_args=[],
        override_arch=None, target_stage=None, emulator=None, outputs=[],
        install_as=None, no_cache=False, verbose=False, quiet=True,
    )


@pytest.mark.parametrize("ref", [
    "http://[",                  # install <url>
    "https://[::1/rootfs.tar",
])
def test_install_malformed_url_is_a_message(ref, capsys):
    with pytest.raises(SystemExit) as exc:
        command_install(_install_args(ref, "badurl"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Failed to install" in err
    assert "not a valid URL" in err
    # The half-made container went with the failure.
    assert not os.path.lexists(container_dir("badurl"))


def _no_pull(monkeypatch):
    def pull(*_a, **_k):
        raise AssertionError("a reference the grammar refuses was pulled")
    monkeypatch.setattr(install_mod, "pull_image", pull)


@pytest.mark.parametrize("ref", [
    "[:5000/img",                # an image reference's registry host
    "[::1/img:1",
])
def test_install_malformed_reference_is_a_message(ref, capsys, monkeypatch):
    # urllib used to be what refused these, from inside the request;
    # the reference grammar refuses them now, before a container name is
    # derived, a lock taken or a directory made.
    _no_pull(monkeypatch)
    with pytest.raises(SystemExit) as exc:
        command_install(_install_args(ref, "badurl"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"cannot install '{ref}'" in err
    assert "invalid reference format" in err
    assert not os.path.lexists(container_dir("badurl"))


def test_install_reference_that_is_not_text_is_a_message(monkeypatch):
    # A byte on the command line that is not UTF-8 arrives as a lone
    # surrogate; deriving the manifest-cache key from it raised
    # UnicodeEncodeError before anything else could look at it. No
    # registry host holds one, so the grammar refuses it before any
    # request is built, and the message spells the byte out.
    #
    # stderr as the interpreter sets it up (backslashreplace), rather than
    # capsys's strict stream, since the reference is echoed back.
    _no_pull(monkeypatch)
    raw = io.BytesIO()
    stderr = io.TextIOWrapper(raw, encoding="utf-8",
                              errors="backslashreplace")
    monkeypatch.setattr(sys, "stderr", stderr)
    with pytest.raises(SystemExit) as exc:
        command_install(_install_args("reg\udcff.example/img", "badurl"))
    assert exc.value.code == 1
    stderr.flush()
    err = raw.getvalue().decode()
    assert "cannot install 'reg\\udcff.example/img'" in err
    assert "invalid reference format" in err
    assert not os.path.lexists(container_dir("badurl"))


def test_build_malformed_reference_is_a_message(tmp_path, capsys,
                                               monkeypatch):
    from proot_distro.helpers.build_engine import engine

    def pull(*_a, **_k):
        raise AssertionError("a refused reference was pulled")

    monkeypatch.setattr(engine, "pull_image", pull)
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text("FROM [:5000/img\n")
    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(ctx))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Build failed: FROM [:5000/img: invalid reference format" in err


@pytest.mark.parametrize("dockerfile", [
    "FROM scratch\nADD https://[ /x\n",
    "FROM scratch\nADD https://[/ /x/\n",    # the name is taken from the URL
])
def test_build_malformed_url_is_a_message(tmp_path, dockerfile, capsys):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(dockerfile)
    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(ctx))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Build failed" in err
    assert "valid URL" in err
