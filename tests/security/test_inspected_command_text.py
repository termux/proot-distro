# Terminal-escape containment for `--get-proot-cmd`.
#
# The inspected command embeds the image's Entrypoint and Cmd, which the
# container can rewrite in its manifest, and it was written to stdout
# byte for byte, so that it can be run or compared. On a terminal, an
# escape sequence in it was obeyed. It is shown escaped there now, with a
# warning that says so; written to a file or a pipe it is still exact.

import io
import sys
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.run import command_run

HOST_ARCH = get_device_cpu_arch()

# An OSC that retitles the terminal, a C1 CSI, and a right-to-left override.
HOSTILE = "x\x1b]0;pwned\x07\x9b2J\N{RIGHT-TO-LEFT OVERRIDE}y"
QUOTED = "x\\e]0;pwned\\x07\\x9b2J\\u202ey"


class _Stdout(io.TextIOWrapper):
    """A stdout whose bytes can be read back, a terminal or not."""

    def __init__(self, terminal):
        super().__init__(io.BytesIO(), encoding="utf-8")
        self._terminal = terminal

    def isatty(self):
        return self._terminal


def _inspect(builders, monkeypatch, capsys, *, terminal):
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": "x:1", "arch": HOST_ARCH,
        "manifest": {"schemaVersion": 2, "layers": []},
        "image_config": {"config": {"Entrypoint": ["/bin/echo", HOSTILE]}},
    })
    stdout = _Stdout(terminal)
    monkeypatch.setattr(sys, "stdout", stdout)
    with pytest.raises(SystemExit) as exc:
        command_run(SimpleNamespace(
            container_name="box", run_args=[], get_proot_cmd=True,
            user="root", kernel=None, hostname="localhost", work_dir="",
            redirect_ports=False, isolated=False, minimal=False,
            shared_home=False, shared_tmp=False, shared_x11=False,
            no_link2symlink=False, no_sysvipc=False, no_kill_on_exit=False,
            bind=[], env=[], login_cmd=[], emulator=None, detach=False,
        ))
    assert exc.value.code == 0
    stdout.flush()
    return stdout.buffer.getvalue(), capsys.readouterr().err


def test_an_inspected_command_is_shown_escaped_on_a_terminal(
        builders, monkeypatch, capsys):
    out, err = _inspect(builders, monkeypatch, capsys, terminal=True)
    for char in ("\x1b", "\x07", "\x9b", "\N{RIGHT-TO-LEFT OVERRIDE}"):
        assert char.encode("utf-8") not in out
    assert QUOTED.encode() in out
    assert "shown escaped" in err


def test_an_inspected_command_is_exact_in_a_file(
        builders, monkeypatch, capsys):
    out, err = _inspect(builders, monkeypatch, capsys, terminal=False)
    assert HOSTILE.encode("utf-8") in out
    assert "shown escaped" not in err
