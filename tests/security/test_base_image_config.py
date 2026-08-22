# Containment tests for a base image's own config document.
#
# `FROM <image>` adopts a config this program did not write: a registry
# sends one, and the manifest cache holds one — a cache that on Termux
# sits under the bound $TERMUX_PREFIX and so is a guest's to compose.
# Every field below is read back afterwards (User and Shell decide what a
# RUN step runs and who as, WorkingDir becomes proot's --cwd, OnBuild is
# parsed as Dockerfile lines, the rest are merged into by their handlers
# and published in the image the build produces), and every consumer
# subscripted it as the type OCI says it is. So a wrong type is a message
# naming the field, never a traceback and never a value carried on.

import os
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.build import command_build
from proot_distro.helpers.build_engine.engine import _adopt_image_config
from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA


HOST_ARCH = get_device_cpu_arch()


# ---------------------------------------------------------------------------
# The shapes a wrong type takes
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("doc", [
    "a config",
    ["a config"],
    5,
    None,
])
def test_a_document_that_is_not_an_object_is_refused(doc):
    with pytest.raises(BuildError):
        _adopt_image_config(doc, "img:1")


@pytest.mark.parametrize("cfg", ["x", ["x"], 5, True])
def test_config_that_is_not_an_object_is_refused(cfg):
    with pytest.raises(BuildError) as exc:
        _adopt_image_config({"config": cfg}, "img:1")
    assert "'config'" in str(exc.value)


@pytest.mark.parametrize("key", ["Cmd", "Entrypoint", "OnBuild", "Shell"])
@pytest.mark.parametrize("value", ["sh", 5, {"a": 1}, ["ok", 5]])
def test_argv_fields_must_be_lists_of_strings(key, value):
    with pytest.raises(BuildError) as exc:
        _adopt_image_config({"config": {key: value}}, "img:1")
    assert key in str(exc.value)


@pytest.mark.parametrize("key", ["User", "WorkingDir"])
@pytest.mark.parametrize("value", [5, ["root"], {"name": "root"}])
def test_user_and_workdir_must_be_strings(key, value):
    with pytest.raises(BuildError) as exc:
        _adopt_image_config({"config": {key: value}}, "img:1")
    assert key in str(exc.value)


@pytest.mark.parametrize("key", ["ExposedPorts", "Labels", "Volumes"])
@pytest.mark.parametrize("value", ["x", ["x"], 5])
def test_maps_must_be_objects(key, value):
    with pytest.raises(BuildError) as exc:
        _adopt_image_config({"config": {key: value}}, "img:1")
    assert key in str(exc.value)


def test_a_label_value_that_is_not_a_string_is_refused():
    with pytest.raises(BuildError):
        _adopt_image_config({"config": {"Labels": {"a": [1]}}}, "img:1")


def test_env_must_be_a_list_of_strings():
    with pytest.raises(BuildError):
        _adopt_image_config({"config": {"Env": "A=1"}}, "img:1")
    with pytest.raises(BuildError):
        _adopt_image_config({"config": {"Env": ["A=1", 2]}}, "img:1")


@pytest.mark.parametrize("value", ["nope", 5, {"a": 1}])
def test_history_must_be_a_list(value):
    with pytest.raises(BuildError) as exc:
        _adopt_image_config({"history": value, "config": {}}, "img:1")
    assert "history" in str(exc.value)


def test_rootfs_and_its_diff_ids_are_held_to_shape():
    with pytest.raises(BuildError):
        _adopt_image_config({"rootfs": "layers", "config": {}}, "img:1")
    with pytest.raises(BuildError):
        _adopt_image_config(
            {"rootfs": {"diff_ids": "sha256:x"}, "config": {}}, "img:1",
        )
    with pytest.raises(BuildError):
        _adopt_image_config(
            {"rootfs": {"diff_ids": ["sha256:x", 7]}, "config": {}}, "img:1",
        )


# ---------------------------------------------------------------------------
# What a registry really sends
# ---------------------------------------------------------------------------

def test_null_is_not_set_and_is_removed_outright():
    # `.get(k) or default` and `setdefault(k, default)` do not answer
    # alike for a null: _record_history's setdefault found the key and
    # appended to None.
    doc = _adopt_image_config({
        "history": None,
        "rootfs": None,
        "config": {
            "Env": None, "Cmd": None, "Entrypoint": None, "Shell": None,
            "OnBuild": None, "User": None, "WorkingDir": None,
            "Labels": None, "ExposedPorts": None, "Volumes": None,
        },
    }, "img:1")
    assert "history" not in doc and "rootfs" not in doc
    assert doc["config"] == {}


def test_an_ordinary_config_survives_unchanged():
    doc = _adopt_image_config({
        "architecture": "amd64",
        "os": "linux",
        "history": [{"created_by": "FROM x"}],
        "rootfs": {"type": "layers", "diff_ids": ["sha256:" + "a" * 64]},
        "config": {
            "Env": ["PATH=/usr/bin", "LANG=C.UTF-8"],
            "Cmd": ["/bin/sh"],
            "Entrypoint": ["/entry"],
            "Shell": ["/bin/bash", "-c"],
            "OnBuild": ["RUN echo hi"],
            "User": "app",
            "WorkingDir": "/srv",
            "Labels": {"maintainer": "someone", "empty": None},
            "ExposedPorts": {"80/tcp": {}},
            "Volumes": {"/data": {}},
        },
    }, "img:1")
    cfg = doc["config"]
    assert cfg["Env"] == ["PATH=/usr/bin", "LANG=C.UTF-8"]
    assert cfg["Cmd"] == ["/bin/sh"]
    assert cfg["Entrypoint"] == ["/entry"]
    assert cfg["Shell"] == ["/bin/bash", "-c"]
    assert cfg["OnBuild"] == ["RUN echo hi"]
    assert cfg["User"] == "app" and cfg["WorkingDir"] == "/srv"
    # A null label value is the empty string, as it is to every other
    # reader of a map of strings.
    assert cfg["Labels"] == {"maintainer": "someone", "empty": ""}
    assert cfg["ExposedPorts"] == {"80/tcp": {}}
    assert cfg["Volumes"] == {"/data": {}}
    assert doc["history"] == [{"created_by": "FROM x"}]
    assert doc["architecture"] == "amd64"


def test_a_port_or_volume_value_of_any_shape_becomes_the_empty_object():
    doc = _adopt_image_config({
        "config": {"ExposedPorts": {"80/tcp": 5}, "Volumes": {"/d": "x"}},
    }, "img:1")
    assert doc["config"]["ExposedPorts"] == {"80/tcp": {}}
    assert doc["config"]["Volumes"] == {"/d": {}}


# ---------------------------------------------------------------------------
# End to end: a hostile cache entry is a message, not a traceback
# ---------------------------------------------------------------------------

def _seed_base_image(builders, image_ref, image_config):
    digest, size, diff_id = builders.seed_cached_layer([
        {"name": "etc/hostname", "type": "file", "data": b"base\n"},
    ])
    manifest = {
        "schemaVersion": 2,
        "layers": [{"digest": digest, "size": size,
                    "mediaType": OCI_LAYER_MEDIA}],
    }
    save_manifest_cache(image_ref, HOST_ARCH, manifest, "library/base",
                        image_config)


def _build_args(ctx):
    return SimpleNamespace(
        path=str(ctx), dockerfile=None, tags=["out:1"], build_args=[],
        override_arch=None, target_stage=None, emulator=None, outputs=[],
        install_as=None, no_cache=False, verbose=False, quiet=True,
    )


def _context(tmp_path, dockerfile):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(dockerfile)
    return ctx


def test_a_hostile_cached_config_ends_the_build_with_a_message(
    tmp_path, builders, capsys
):
    _seed_base_image(builders, "base:1", {"config": {"OnBuild": 5}})
    ctx = _context(tmp_path, "FROM base:1\nLABEL a=b\n")

    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(ctx))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Build failed" in err and "OnBuild" in err


def test_a_well_shaped_cached_config_still_builds(tmp_path, builders):
    _seed_base_image(builders, "base:ok", {
        "config": {"Env": ["A=1"], "Cmd": ["/bin/sh"], "Labels": None},
        "rootfs": {"diff_ids": ["sha256:" + "b" * 64]},
    })
    ctx = _context(tmp_path, "FROM base:ok\nLABEL a=b\n")

    before = set(os.listdir("/proc/self/fd"))
    command_build(_build_args(ctx))
    assert set(os.listdir("/proc/self/fd")) - before == set()
