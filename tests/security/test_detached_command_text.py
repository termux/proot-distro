# Terminal-escape containment for a detached session's summary.
#
# `run --detach` and `login --detach` print "Command: ..." for the session
# they started: the image's Entrypoint and Cmd, unless arguments replaced
# them, joined by shlex. shlex quotes for a shell and leaves control
# characters in, and the Entrypoint and Cmd come from the container's
# manifest, which the container can rewrite. An escape sequence there was
# obeyed by the terminal. The joined command is quoted now (quote_path).

from types import SimpleNamespace

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands import session as session_command
from proot_distro.commands.run import command_run

HOST_ARCH = get_device_cpu_arch()

# An OSC that retitles the terminal, a C1 CSI, and a right-to-left override.
HOSTILE = "x\x1b]0;pwned\x07\x9b2J\u202ey"
QUOTED = "x\\e]0;pwned\\x07\\x9b2J\\u202ey"
CONTROLS = ("\x1b", "\x07", "\x9b", "\u202e")


def _assert_no_controls(text):
    for char in CONTROLS:
        assert char not in text, repr(text)


def test_a_detached_sessions_command_is_shown_not_obeyed(
        builders, monkeypatch, capsys):
    builders.make_container("box", arch=HOST_ARCH, manifest={
        "image_ref": "x:1", "arch": HOST_ARCH,
        "manifest": {"schemaVersion": 2, "layers": []},
        "image_config": {"config": {"Entrypoint": ["/bin/echo", HOSTILE]}},
    })
    # Everything real but the fork into proot.
    monkeypatch.setattr(session_command, "detach_session",
                        lambda prepared, **_kwargs: 4242)
    command_run(SimpleNamespace(
        container_name="box", run_args=[], get_proot_cmd=False,
        user="root", kernel=None, hostname="localhost", work_dir="",
        redirect_ports=False, isolated=False, minimal=False,
        shared_home=False, shared_tmp=False, shared_x11=False,
        no_link2symlink=False, no_sysvipc=False, no_kill_on_exit=False,
        bind=[], env=[], login_cmd=[], emulator=None, detach=True,
    ))
    err = capsys.readouterr().err
    _assert_no_controls(err)
    assert "4242" in err
    assert QUOTED in err
