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
    "[:5000/img",                # an image reference's registry host
    "[::1/img:1",
])
def test_install_malformed_reference_is_a_message(ref, capsys):
    with pytest.raises(SystemExit) as exc:
        command_install(_install_args(ref, "badurl"))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Failed to install" in err
    assert "not a valid URL" in err
    # The half-made container went with the failure.
    assert not os.path.lexists(container_dir("badurl"))


def test_install_reference_that_is_not_text_is_a_message(monkeypatch):
    # A byte on the command line that is not UTF-8 arrives as a lone
    # surrogate; deriving the manifest-cache key from it raised
    # UnicodeEncodeError before anything else could look at it. The
    # request that follows is refused before it is sent: the host is
    # encoded for the Host header ahead of any connection.
    #
    # stderr as the interpreter sets it up (backslashreplace), rather than
    # capsys's strict stream, since the reference is echoed back.
    raw = io.BytesIO()
    stderr = io.TextIOWrapper(raw, encoding="utf-8",
                              errors="backslashreplace")
    monkeypatch.setattr(sys, "stderr", stderr)
    with pytest.raises(SystemExit) as exc:
        command_install(_install_args("reg\udcff.example/img", "badurl"))
    assert exc.value.code == 1
    stderr.flush()
    err = raw.getvalue().decode()
    assert "Failed to install" in err
    assert "cannot send a request" in err
    assert not os.path.lexists(container_dir("badurl"))


@pytest.mark.parametrize("dockerfile", [
    "FROM [:5000/img\n",
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
