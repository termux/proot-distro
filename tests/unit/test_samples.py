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
