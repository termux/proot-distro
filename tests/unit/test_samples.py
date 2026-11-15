import importlib.util
from pathlib import Path
from types import SimpleNamespace

from proot_distro.api import CredentialRequest, EventKind, OperationEvent


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


def test_tui_fit_uses_display_width():
    module = _load("container_manager_tui")

    assert module._fit("ab", 4) == "ab  "
    assert module._fit("abcdef", 4) == "abcd"
    # A wide character takes two columns and is never split.
    assert module._fit("中文", 3) == "中 "
    assert module._fit("éx", 2) == "éx"


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
