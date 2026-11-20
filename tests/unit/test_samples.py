import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from proot_distro.api import (
    CredentialRequest,
    EventKind,
    OperationCancelledError,
    OperationEvent,
    ProotDistroError,
)


SAMPLES = Path(__file__).parents[2] / "samples"


def _load(name):
    path = SAMPLES / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"sample_{name}", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_list_containers_is_a_small_read_only_example(monkeypatch, capsys):
    module = _load("list_containers")

    class FakeApi:
        def list_containers(self):
            return (SimpleNamespace(
                name="demo",
                image_reference="alpine:latest",
                architecture="arm64",
            ),)

    monkeypatch.setattr(module, "ProotDistro", FakeApi)

    assert module.main() == 0
    assert (
        "- demo: alpine:latest (arm64)"
        in capsys.readouterr().out
    )


def test_install_container_passes_image_and_name(monkeypatch, capsys):
    module = _load("install_container")
    calls = []

    class FakeApi:
        def install(self, image, *, name):
            calls.append((image, name))
            return SimpleNamespace(
                name=name,
                image_reference=image,
                architecture="amd64",
            )

    monkeypatch.setattr(module, "ProotDistro", FakeApi)

    assert module.main(["alpine:latest", "demo"]) == 0
    assert calls == [("alpine:latest", "demo")]
    assert (
        "Installed demo from alpine:latest (amd64)."
        in capsys.readouterr().out
    )


def test_run_command_uses_a_managed_foreground_process(monkeypatch):
    module = _load("run_command")
    calls = []

    class FakeProcess:
        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            calls.append("exit")

        def wait(self):
            calls.append("wait")
            return 0

    class FakeApi:
        def start(self, container, command):
            calls.append((container, command))
            return FakeProcess()

    monkeypatch.setattr(module, "ProotDistro", FakeApi)

    assert module.main(["demo", "--", "uname", "-a"]) == 0
    assert calls == [
        ("demo", ["uname", "-a"]),
        "enter",
        "wait",
        "exit",
    ]


def test_container_manager_tui_installs_lists_and_removes(monkeypatch):
    module = _load("container_manager_tui")
    items = [SimpleNamespace(
        name="first",
        image_reference="alpine:latest",
        architecture="arm64",
    )]
    calls = []

    class FakeScreen:
        def keypad(self, enabled):
            calls.append(("keypad", enabled))

        def timeout(self, value):
            calls.append(("timeout", value))

        def getmaxyx(self):
            return 24, 80

    class FakeApi:
        def list_containers(self):
            calls.append("list")
            return tuple(items)

        def install(self, source, *, name):
            calls.append(("install", source, name))
            container = SimpleNamespace(
                name=name,
                image_reference=source,
                architecture="amd64",
            )
            items.append(container)
            return container

        def remove_container(self, name):
            calls.append(("remove", name))
            items[:] = [item for item in items if item.name != name]

    monkeypatch.setattr(module.curses, "has_colors", lambda: False)
    monkeypatch.setattr(module.curses, "curs_set", lambda value: None)
    manager = module.ContainerManager(FakeScreen(), FakeApi())
    monkeypatch.setattr(manager, "_draw", lambda: None)
    manager._reload_containers()

    answers = iter(("debian:stable", "second"))
    monkeypatch.setattr(manager, "_prompt", lambda label: next(answers))
    manager._install_container()
    assert [item.name for item in manager.containers] == ["first", "second"]
    assert manager.containers[manager.selected].name == "second"

    monkeypatch.setattr(manager, "_confirm", lambda question: True)
    manager._remove_container()
    assert [item.name for item in manager.containers] == ["first"]
    assert ("install", "debian:stable", "second") in calls
    assert ("remove", "second") in calls


def test_container_manager_tui_restores_terminal_after_shell(monkeypatch):
    module = _load("container_manager_tui")
    calls = []
    container = SimpleNamespace(
        name="demo",
        image_reference="alpine:latest",
        architecture="arm64",
    )

    class FakeScreen:
        def keypad(self, enabled):
            calls.append(("keypad", enabled))

        def timeout(self, value):
            calls.append(("timeout", value))

        def getmaxyx(self):
            return 24, 80

        def clear(self):
            calls.append("clear")

        def refresh(self):
            calls.append("refresh")

    class FakeProcess:
        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            calls.append("exit")

        def wait(self):
            calls.append("wait")
            return 0

    class FakeApi:
        def list_containers(self):
            return (container,)

        def start(self, name):
            calls.append(("start", name))
            return FakeProcess()

    monkeypatch.setattr(module.curses, "has_colors", lambda: False)
    for name in (
        "curs_set",
        "def_prog_mode",
        "endwin",
        "reset_prog_mode",
        "noecho",
        "cbreak",
    ):
        monkeypatch.setattr(module.curses, name, lambda *args: None)

    manager = module.ContainerManager(FakeScreen(), FakeApi())
    manager._reload_containers()
    monkeypatch.setattr(manager, "_draw", lambda: None)
    manager._launch_shell()

    assert ("start", "demo") in calls
    assert calls.index("enter") < calls.index("wait") < calls.index("exit")
    assert "clear" in calls
    assert manager.status == "Shell in demo closed."
    assert manager.terminal_suspended is False


def test_list_resources_displays_inventory_and_optional_search(monkeypatch, capsys):
    module = _load("list_resources")

    class FakeApi:
        def list_containers(self):
            return (SimpleNamespace(
                name="demo", image_reference="alpine:3.21",
                architecture="arm64",
            ),)

        def list_images(self):
            return (SimpleNamespace(
                reference="alpine:3.21", identifier="cached-image",
                complete=True, size_bytes=2048, layer_count=2,
            ),)

        def list_sessions(self):
            return (SimpleNamespace(
                pid=123, container="demo", kind="login",
                detached=False, command=("uname", "-a"),
            ),)

        def search(self, query, *, limit):
            assert (query, limit) == ("alpine", 3)
            return SimpleNamespace(
                total=1,
                hits=(SimpleNamespace(
                    name="library/alpine", official=True,
                    description="A minimal image",
                ),),
            )

    monkeypatch.setattr(module, "ProotDistro", FakeApi)
    assert module.main(["--search", "alpine", "--limit", "3"]) == 0
    output = capsys.readouterr().out
    assert "demo: alpine:3.21 [arm64]" in output
    assert "alpine:3.21: 2.0 KiB, 2 layers, complete" in output
    assert "pid 123: demo [login, foreground] uname -a" in output
    assert "library/alpine [official]: A minimal image" in output


def test_install_and_run_uses_process_context_and_optional_cleanup(
    monkeypatch, capsys,
):
    module = _load("install_and_run")
    calls = []

    class FakeProcess:
        returncode = 0

        def __enter__(self):
            calls.append("enter")
            return self

        def __exit__(self, exc_type, exc_value, traceback):
            calls.append("exit")

        def communicate(self, *, timeout):
            assert timeout == 8
            return b"sample output\n", b""

    class FakeApi:
        def install(self, source, *, name, architecture):
            calls.append(("install", source, name, architecture))
            return SimpleNamespace(
                name=name, image_reference=source, architecture=architecture,
            )

        def inspect_start(self, name, command, *, environment):
            calls.append(("inspect", name, tuple(command), environment))
            return SimpleNamespace(inner_command=tuple(command), work_dir="/root")

        def start(self, name, command, **options):
            calls.append(("start", name, tuple(command), options))
            return FakeProcess()

        def remove_container(self, name):
            calls.append(("remove", name))

    monkeypatch.setattr(module, "ProotDistro", FakeApi)
    assert module.main([
        "image:1", "demo", "--remove", "--command-timeout", "8",
        "--", "uname", "-a",
    ]) == 0
    assert calls[0] == ("install", "image:1", "demo", None)
    assert calls[-1] == ("remove", "demo")
    assert calls.count("enter") == calls.count("exit") == 1
    assert "sample output" in capsys.readouterr().out


def test_stream_archives_hashes_backup_and_restore(monkeypatch, tmp_path, capsys):
    module = _load("stream_archives")
    archive = tmp_path / "demo.tar.gz"
    restored = []

    class FakeApi:
        def backup(self, container, stream, *, compression):
            assert (container, compression) == ("demo", "gzip")
            assert stream.write(b"archive-data") == len(b"archive-data")
            return SimpleNamespace(container=container, compression=compression)

        def restore(self, stream):
            restored.append(stream.read())
            return SimpleNamespace(container=SimpleNamespace(name="demo"))

    monkeypatch.setattr(module, "ProotDistro", FakeApi)
    assert module.main(["backup", "demo", str(archive)]) == 0
    assert archive.read_bytes() == b"archive-data"
    backup_output = capsys.readouterr().out
    assert "Wrote 12 bytes for demo using gzip" in backup_output
    assert "SHA-256:" in backup_output

    assert module.main(["restore", str(archive)]) == 0
    assert restored == [b"archive-data"]
    assert "Restored demo from 12 bytes" in capsys.readouterr().out


def test_build_image_wires_events_credentials_config_and_push(
    monkeypatch, tmp_path, capsys,
):
    module = _load("build_image")
    runtime = tmp_path / "runtime"
    cache = tmp_path / "cache"
    captured = {}

    monkeypatch.setenv("PD_SAMPLE_REGISTRY", "registry.example")
    monkeypatch.setenv("PD_SAMPLE_REGISTRY_USERNAME", "sample-user")
    monkeypatch.setenv("PD_SAMPLE_REGISTRY_TOKEN", "sample-token")

    class FakeApi:
        def __init__(self, config, *, event_handler, credential_provider):
            captured["config"] = config
            captured["provider"] = credential_provider
            event_handler(OperationEvent(
                "build", EventKind.PROGRESS, "packing", 1, 2, "layers",
                "layer-pack", "example/app:1",
            ))

        def build(self, context, **options):
            captured["build"] = (context, options)
            return SimpleNamespace(
                images=(SimpleNamespace(
                    reference="registry.example/app:1", identifier="image-id",
                    size_bytes=10, layer_count=1,
                ),),
                outputs=(),
                installed_container=None,
            )

        def push(self, tag, **options):
            captured["push"] = (tag, options)
            return SimpleNamespace(
                registry="registry.example", repository="app", tag="1",
                manifest_digest="sha256:abc", bytes_uploaded=10,
            )

    monkeypatch.setattr(module, "ProotDistro", FakeApi)
    assert module.main([
        str(tmp_path), "--tag", "registry.example/app:1",
        "--build-arg", "VERSION=1", "--runtime-dir", str(runtime),
        "--cache-dir", str(cache), "--push",
    ]) == 0

    assert captured["config"].runtime_dir == str(runtime)
    assert captured["config"].cache_dir == str(cache)
    request = CredentialRequest("registry.example", "app", "build")
    credentials = captured["provider"](request)
    assert credentials.username == "sample-user"
    assert credentials.password == "sample-token"
    assert captured["build"][1]["build_args"] == {"VERSION": "1"}
    assert captured["push"][0] == "registry.example/app:1"
    output = capsys.readouterr().out
    assert "[build/layer-pack] 1/2 layers: packing" in output
    assert "Pushed registry.example/app:1 as sha256:abc" in output


def _process_returning(code):
    class FakeProcess:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def wait(self):
            return code

    return FakeProcess()


def test_run_command_passes_guest_status_through_on_stderr(
    monkeypatch, capsys,
):
    """Regression: the guest's exit status used to collapse to 1."""
    module = _load("run_command")

    class FakeApi:
        def start(self, container, command):
            return _process_returning(7)

    monkeypatch.setattr(module, "ProotDistro", FakeApi)

    assert module.main(["demo", "false"]) == 7
    captured = capsys.readouterr()
    assert captured.out == ""
    assert "status 7" in captured.err


def test_run_command_maps_signal_death_to_shell_status(monkeypatch):
    module = _load("run_command")

    class FakeApi:
        def start(self, container, command):
            return _process_returning(-9)

    monkeypatch.setattr(module, "ProotDistro", FakeApi)

    assert module.main(["demo", "sleep", "9"]) == 137


def test_samples_report_keyboard_interrupt_without_traceback(
    monkeypatch, capsys,
):
    """Regression: Ctrl-C used to print a raw traceback in these samples."""
    for name, argv, method in (
        ("install_container", ["alpine", "box"], "install"),
        ("list_containers", [], "list_containers"),
    ):
        module = _load(name)

        class FakeApi:
            def __getattr__(self, _name):
                def interrupted(*_args, **_kwargs):
                    raise KeyboardInterrupt
                return interrupted

        monkeypatch.setattr(module, "ProotDistro", FakeApi)
        call = module.main(argv) if argv else module.main()
        assert call == 130, name
    assert "Traceback" not in capsys.readouterr().err


def test_install_and_run_handles_interrupt_and_cleans_up(
    monkeypatch, capsys,
):
    module = _load("install_and_run")
    removed = []

    class FakeApi:
        def install(self, *_args, **_kwargs):
            return SimpleNamespace(name="box", image_reference="img")

        def inspect_start(self, *_args, **_kwargs):
            raise KeyboardInterrupt

        def remove_container(self, name):
            removed.append(name)

    monkeypatch.setattr(module, "ProotDistro", FakeApi)

    assert module.main(["alpine", "box", "--remove"]) == 130
    assert removed == ["box"]
    assert "Interrupted" in capsys.readouterr().err


def test_stream_archives_interrupt_removes_partial_backup(
    monkeypatch, tmp_path, capsys,
):
    module = _load("stream_archives")
    archive = tmp_path / "partial.tar.gz"

    class FakeApi:
        def backup(self, container, stream, *, compression):
            stream.write(b"partial")
            raise KeyboardInterrupt

    monkeypatch.setattr(module, "ProotDistro", FakeApi)

    assert module.main(["backup", "demo", str(archive)]) == 130
    assert not archive.exists()
    assert "Traceback" not in capsys.readouterr().err


def test_list_resources_strips_terminal_escapes_from_search_hits(
    monkeypatch, capsys,
):
    """A hostile Hub description must not reach the terminal raw."""
    module = _load("list_resources")

    class FakeApi:
        def list_containers(self):
            return ()

        def list_images(self):
            return ()

        def list_sessions(self):
            return ()

        def search(self, query, *, limit):
            hit = SimpleNamespace(
                name="evil\x1b]0;pwned\x07", official=False,
                description="hi\x1b[2Jthere",
            )
            return SimpleNamespace(total=1, hits=(hit,))

    monkeypatch.setattr(module, "ProotDistro", FakeApi)

    assert module.main(["--search", "evil"]) == 0
    output = capsys.readouterr().out
    assert "\x1b" not in output and "\x07" not in output
    assert "hi?[2Jthere" in output


def test_list_resources_strips_escapes_from_recorded_metadata(
    monkeypatch, capsys,
):
    """Regression: container and image references were printed raw.

    A container can rewrite its own manifest.json (and the cache entries
    are under the same guest-writable tree on Termux), so these are a
    third party's text as much as a Hub description is.
    """
    module = _load("list_resources")

    class FakeApi:
        def list_containers(self):
            return (SimpleNamespace(
                name="box", image_reference="evil\x1b]0;pwned\x07:1",
                architecture="x86\x1b[2J64",
            ),)

        def list_images(self):
            return (SimpleNamespace(
                reference="img\x1b[31m:1", identifier="abc", complete=True,
                size_bytes=1, layer_count=1,
            ),)

        def list_sessions(self):
            return ()

    monkeypatch.setattr(module, "ProotDistro", FakeApi)

    assert module.main([]) == 0
    output = capsys.readouterr().out
    assert "\x1b" not in output and "\x07" not in output
    assert "evil?]0;pwned?:1" in output


def test_install_and_run_strips_escapes_from_guest_derived_fields(
    monkeypatch, capsys,
):
    """The inspected shell and home come from the guest's /etc/passwd."""
    module = _load("install_and_run")

    class FakeProcess:
        returncode = 0

        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def communicate(self, *, timeout):
            return b"", b""

    class FakeApi:
        def install(self, source, *, name, architecture):
            return SimpleNamespace(
                name=name, image_reference=source, architecture=None,
            )

        def inspect_start(self, name, command, *, environment):
            return SimpleNamespace(
                inner_command=("/bin/\x1b]0;x\x07sh", "-c", "true"),
                work_dir="/home/\x1b[2Jme",
            )

        def start(self, name, command, **options):
            return FakeProcess()

    monkeypatch.setattr(module, "ProotDistro", FakeApi)
    assert module.main(["image:1", "demo"]) == 0
    output = capsys.readouterr().out
    assert "\x1b" not in output and "\x07" not in output


def test_tui_fit_uses_display_width():
    module = _load("container_manager_tui")

    assert module._fit("ab", 4) == "ab  "
    assert module._fit("abcdef", 4) == "abcd"
    # A wide character takes two columns and is never split.
    assert module._fit("中文", 3) == "中 "
    assert module._fit("éx", 2) == "éx"


def test_tui_printable_replaces_what_a_terminal_acts_on():
    module = _load("container_manager_tui")

    assert module._printable(
        "a\x00b\nc\td\re\bf\x1b[2J\x9b\u202e\u2066\udc80\ue000"
    ) == "a?b c d e?f?[2J?????"
    # Printable text, wide and combining characters included, is kept.
    assert module._printable("alpine:3.21 \u4e2d e\u0301") == (
        "alpine:3.21 \u4e2d e\u0301"
    )


def test_tui_fit_counts_controls_and_zero_width_marks():
    module = _load("container_manager_tui")

    assert module._fit("a\nb", 4) == "a b "
    assert module._fit("ab\x1b", 4) == "ab? "
    # Marks with no combining class still draw over the previous cell.
    assert module._fit("a\u20dd", 2) == "a\u20dd "
    assert module._fit("a\u034fb", 2) == "a\u034fb"


def test_tui_draws_every_string_printable_and_within_its_row(monkeypatch):
    module = _load("container_manager_tui")
    drawn = []

    class FakeScreen:
        def keypad(self, enabled):
            pass

        def timeout(self, value):
            pass

        def getmaxyx(self):
            return 24, 80

        def erase(self):
            pass

        def refresh(self):
            pass

        def addstr(self, y, x, text, attribute=0):
            drawn.append((y, x, text))

    class FakeApi:
        def list_containers(self):
            return (SimpleNamespace(
                name="box", image_reference="evil\x00\n\u4e2d" * 30,
                architecture="x\r\x1b",
            ),)

    monkeypatch.setattr(module.curses, "has_colors", lambda: False)
    monkeypatch.setattr(module.curses, "curs_set", lambda value: None)
    manager = module.ContainerManager(FakeScreen(), FakeApi())
    manager._reload_containers()
    manager._handle_event(OperationEvent(
        "install", EventKind.PROGRESS, "\u4e2d\n" * 80,
    ))

    assert drawn
    for y, x, text in drawn:
        assert text.isprintable(), (y, text)
        assert x + sum(map(module._cell_width, text)) <= 80, (y, text)


def _raw_error(error_type, text, **kwargs):
    """An *error_type* whose str() is *text*, controls and all.

    ProotDistroError escapes its message when it is made, so no error
    the library raises carries raw controls any more. The samples clean
    what they print all the same: each stands alone, may run against
    another version of the library, and catches more than its errors.
    So the raw text is put back after the error is made.
    """
    error = error_type("placeholder", **kwargs)
    error.args = (text,)
    return error


def test_tui_cleans_the_error_it_prints_after_curses(monkeypatch, capsys):
    module = _load("container_manager_tui")

    class Tty:
        def isatty(self):
            return True

    def fail(_main):
        raise _raw_error(module.ProotDistroError, "bad\x1b]0;title\x07 value")

    monkeypatch.setattr(module.sys, "stdin", Tty())
    monkeypatch.setattr(module.sys, "stdout", Tty())
    monkeypatch.setattr(module.curses, "wrapper", fail)

    assert module.main() == 1
    assert "bad?]0;title? value" in capsys.readouterr().err


def test_tui_cleans_the_name_it_prints_before_a_shell(monkeypatch, capsys):
    module = _load("container_manager_tui")
    calls = []

    class FakeScreen:
        def keypad(self, enabled):
            pass

        def timeout(self, value):
            pass

        def getmaxyx(self):
            return 24, 80

        def clear(self):
            pass

        def refresh(self):
            pass

    class FakeProcess:
        def __enter__(self):
            return self

        def __exit__(self, *_exc):
            return False

        def wait(self):
            return 0

    class FakeApi:
        # An adapted copy may list containers from another source.
        def start(self, name):
            calls.append(name)
            return FakeProcess()

    monkeypatch.setattr(module.curses, "has_colors", lambda: False)
    for name in ("curs_set", "def_prog_mode", "endwin", "reset_prog_mode",
                 "noecho", "cbreak"):
        monkeypatch.setattr(module.curses, name, lambda *args: None)
    manager = module.ContainerManager(FakeScreen(), FakeApi())
    manager.containers = [SimpleNamespace(name="box\x1b[2J")]
    monkeypatch.setattr(manager, "_draw", lambda: None)
    manager._launch_shell()

    assert calls == ["box\x1b[2J"]
    assert "Opening shell in box?[2J." in capsys.readouterr().out


def test_tui_refuses_to_start_without_a_terminal(monkeypatch, capsys):
    module = _load("container_manager_tui")

    class NotATty:
        def isatty(self):
            return False

    def fail(*_args, **_kwargs):
        raise AssertionError("curses must not start without a terminal")

    monkeypatch.setattr(module.sys, "stdin", NotATty())
    monkeypatch.setattr(module.curses, "wrapper", fail)

    assert module.main() == 2
    assert "interactive terminal" in capsys.readouterr().err


# Text a third party chose: an OSC title sequence (ESC ... BEL), a C1 CSI
# and a right-to-left override. An HTTP reason phrase is decoded as
# Latin-1, so a registry's bytes 0x80-0x9f arrive as C1 controls. The
# library escapes such text in its error and event messages, but results
# and build output carry it as it came, and errors are put together with
# it raw here (_raw_error) for the reasons given there.
HOSTILE = "bad\x1b]0;pwned\x07 \x9b2J\u202eend"
CLEANED = "bad?]0;pwned? ?2J?end"


def _assert_cleaned(text):
    for control in ("\x1b", "\x07", "\x9b", "\u202e"):
        assert control not in text, repr(text)
    assert CLEANED in text, repr(text)


class _FailingApi:
    """Every API method raises an error carrying HOSTILE."""

    def __init__(self, *_args, **_kwargs):
        pass

    def __getattr__(self, method):
        def fail(*_args, **_kwargs):
            raise _raw_error(ProotDistroError, HOSTILE, operation=method)
        return fail


@pytest.mark.parametrize("name, argv", [
    ("install_container", ["alpine", "box"]),
    ("list_containers", None),
    ("list_resources", []),
    ("run_command", ["box"]),
    ("install_and_run", ["alpine", "box"]),
    ("stream_archives", ["backup", "box", "{tmp}/new.tar.gz"]),
    ("stream_archives", ["restore", "{tmp}/old.tar.gz"]),
    ("build_image", ["{tmp}", "--tag", "app:1"]),
])
def test_samples_clean_the_error_they_print(
    monkeypatch, capsys, tmp_path, name, argv,
):
    """Regression: an error's text reached the terminal raw."""
    module = _load(name)
    (tmp_path / "old.tar.gz").write_bytes(b"archive")
    monkeypatch.setattr(module, "ProotDistro", _FailingApi)

    if argv is None:
        status = module.main()
    else:
        status = module.main([arg.format(tmp=tmp_path) for arg in argv])

    assert status == 1
    _assert_cleaned(capsys.readouterr().err)


def test_samples_clean_errors_raised_while_setting_up(monkeypatch, capsys):
    for name, argv in (
        ("build_image", [".", "--tag", "app:1"]),
        ("stream_archives", ["restore", "old.tar.gz"]),
    ):
        module = _load(name)

        def refuse(*_args, **_kwargs):
            raise _raw_error(ProotDistroError, HOSTILE,
                             operation="initialize")

        monkeypatch.setattr(module, "ProotDistro", refuse)
        assert module.main(argv) in (1, 2), name
        _assert_cleaned(capsys.readouterr().err)


def test_build_image_cleans_a_cancellation_message(monkeypatch, capsys):
    module = _load("build_image")

    class CancelledApi(_FailingApi):
        def build(self, *_args, **_kwargs):
            raise _raw_error(OperationCancelledError, HOSTILE,
                             operation="build")

    monkeypatch.setattr(module, "ProotDistro", CancelledApi)

    assert module.main([".", "--tag", "app:1"]) == 2
    _assert_cleaned(capsys.readouterr().err)


def test_install_and_run_cleans_an_error_during_cleanup(monkeypatch, capsys):
    module = _load("install_and_run")

    class Api(_FailingApi):
        def install(self, source, *, name, architecture):
            return SimpleNamespace(
                name=name, image_reference=source, architecture=None,
            )

    monkeypatch.setattr(module, "ProotDistro", Api)

    assert module.main(["alpine", "box", "--remove"]) == 1
    err = capsys.readouterr().err
    assert "failed during cleanup: " + CLEANED in err
    _assert_cleaned(err)


def test_build_image_cleans_event_messages_and_the_pushed_digest(
    monkeypatch, tmp_path, capsys,
):
    """Regression: build events and the pushed digest were printed raw.

    A build-output event is what a RUN step printed. The digest used to
    be the registry's Docker-Content-Digest header; the library works it
    out itself now (test_push_registry_digest.py), and the sample still
    cleans it rather than rely on that.
    """
    module = _load("build_image")

    class Api:
        def __init__(self, _config, *, event_handler, credential_provider):
            event_handler(OperationEvent(
                "build", EventKind.INFO, HOSTILE, phase="build-output",
            ))
            event_handler(OperationEvent(
                "build", EventKind.PROGRESS, HOSTILE, 1, 2, "layers",
                "layer-pack",
            ))

        def build(self, context, **options):
            return SimpleNamespace(
                images=(), outputs=(), installed_container=None,
            )

        def push(self, tag, **options):
            return SimpleNamespace(
                registry="registry.example", repository="app", tag="1",
                manifest_digest=HOSTILE, bytes_uploaded=10,
            )

    monkeypatch.setattr(module, "ProotDistro", Api)

    assert module.main([str(tmp_path), "--tag", "app:1", "--push"]) == 0
    out = capsys.readouterr().out
    _assert_cleaned(out)
    assert out.count(CLEANED) == 3


@pytest.mark.parametrize("name, argv, method", [
    ("list_containers", None, "list_containers"),
    ("install_container", ["alpine", "box"], "install"),
])
def test_samples_clean_recorded_image_metadata(
    monkeypatch, capsys, name, argv, method,
):
    """Regression: an image reference and architecture were printed raw.

    They come from the image, and a container can rewrite its own copy.
    """
    module = _load(name)
    container = SimpleNamespace(
        name="box", image_reference=HOSTILE, architecture=HOSTILE,
    )

    class Api:
        def list_containers(self):
            return (container,)

        def install(self, image, *, name):
            return container

    monkeypatch.setattr(module, "ProotDistro", Api)

    assert (module.main() if argv is None else module.main(argv)) == 0
    out = capsys.readouterr().out
    _assert_cleaned(out)
    assert out.count(CLEANED) == 2


def test_install_and_run_cleans_the_installed_image_reference(
    monkeypatch, capsys,
):
    module = _load("install_and_run")

    class Api(_FailingApi):
        def install(self, source, *, name, architecture):
            return SimpleNamespace(
                name=name, image_reference=HOSTILE, architecture=None,
            )

    monkeypatch.setattr(module, "ProotDistro", Api)

    assert module.main(["alpine", "box"]) == 1
    _assert_cleaned(capsys.readouterr().out)


def test_list_resources_cleans_the_command_lines_of_sessions(
    monkeypatch, capsys,
):
    """Another session's command line is whatever that session was given."""
    module = _load("list_resources")

    class Api:
        def list_containers(self):
            return ()

        def list_images(self):
            return ()

        def list_sessions(self):
            return (SimpleNamespace(
                pid=7, container="box", kind="login", detached=False,
                command=("sh", "-c", HOSTILE),
            ),)

    monkeypatch.setattr(module, "ProotDistro", Api)

    assert module.main([]) == 0
    _assert_cleaned(capsys.readouterr().out)
