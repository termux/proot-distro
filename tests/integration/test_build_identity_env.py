# What a RUN step is told about where it is running.
#
# A container session exports PD_CONTAINER, PD_IMAGE, PD_IMAGE_ID and
# `container=proot-distro` (see test_login_get_proot_cmd). A build has no
# container, and it has no tag a step could be told about either -- `-t`
# is optional and repeatable, and the build cache is keyed on what a step
# can read -- so a RUN step gets the other three, for the image its
# stage's rootfs was materialised from: the FROM reference and that
# image's config digest, inherited by a stage built FROM an earlier one.

import os
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.execenv import CONTAINER_MARKER
from proot_distro.helpers.build_engine import run_step
from proot_distro.helpers.build_engine.engine import BuildEngine
from proot_distro.helpers.build_engine.stage import Stage
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA
from proot_distro.helpers.dockerfile import parse_dockerfile


HOST_ARCH = get_device_cpu_arch()
DIGEST = "sha256:" + "ab" * 32


def _seed_base_image(builders, image_ref, config_digest=DIGEST):
    digest, size, diff_id = builders.seed_cached_layer([
        {"name": "etc/hostname", "type": "file", "data": b"base\n"},
    ])
    manifest = {
        "schemaVersion": 2,
        "layers": [{"digest": digest, "size": size,
                    "mediaType": OCI_LAYER_MEDIA}],
    }
    if config_digest is not None:
        manifest["config"] = {"digest": config_digest}
    save_manifest_cache(image_ref, HOST_ARCH, manifest, "library/base",
                        {"config": {"Env": ["container=oci"]}})


def _engine(tmp_path, dockerfile):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(dockerfile)
    tmp_root = tmp_path / "build-tmp"
    tmp_root.mkdir()
    engine = BuildEngine(
        build_dir=str(ctx), tmp_root=str(tmp_root),
        target_arch_pd=HOST_ARCH, user_build_args={}, target_stage=None,
        verbose=False, quiet=True, no_cache=False, emulator=None,
    )
    _directives, instructions = parse_dockerfile(dockerfile)
    return engine, instructions


# --- what FROM records ------------------------------------------------------

def test_from_records_the_base_image(tmp_path, builders):
    _seed_base_image(builders, "base:1")
    engine, instructions = _engine(tmp_path, "FROM base:1\nLABEL a=b\n")
    try:
        stage = engine.run(instructions)
        assert stage.base_ref == "base:1"
        assert stage.base_image_id == DIGEST
    finally:
        engine.close()


def test_from_scratch_has_no_base_image(tmp_path):
    engine, instructions = _engine(tmp_path, "FROM scratch\nLABEL a=b\n")
    try:
        stage = engine.run(instructions)
        assert stage.base_ref == ""
        assert stage.base_image_id == ""
    finally:
        engine.close()


def test_from_a_stage_inherits_its_base_image(tmp_path, builders):
    _seed_base_image(builders, "base:1")
    engine, instructions = _engine(
        tmp_path, "FROM base:1 AS one\nLABEL a=b\nFROM one\nLABEL c=d\n",
    )
    try:
        stage = engine.run(instructions)
        assert stage.index == 1
        assert stage.base_ref == "base:1"
        assert stage.base_image_id == DIGEST
    finally:
        engine.close()


def test_a_manifest_without_a_usable_config_digest_gives_no_id(
    tmp_path, builders
):
    _seed_base_image(builders, "base:noid", config_digest="../x:y")
    engine, instructions = _engine(tmp_path, "FROM base:noid\nLABEL a=b\n")
    try:
        stage = engine.run(instructions)
        assert stage.base_ref == "base:noid"
        assert stage.base_image_id == ""
    finally:
        engine.close()


# --- what the RUN launcher exports -----------------------------------------

def _stage(tmp_path, **over):
    stage = Stage(index=0, name=None, rootfs_dir=str(tmp_path / "rootfs"),
                  target_arch_pd=HOST_ARCH)
    os.makedirs(stage.rootfs_dir, exist_ok=True)
    for key, val in over.items():
        setattr(stage, key, val)
    return stage


def _child_env(stage):
    engine = SimpleNamespace(warned_host_exec=set())
    return run_step._build_child_env(engine, stage)


def test_run_env_names_the_base_image(tmp_path):
    env = _child_env(_stage(tmp_path, base_ref="debian:bookworm",
                            base_image_id=DIGEST))
    assert env["container"] == CONTAINER_MARKER
    assert env["PD_IMAGE"] == "debian:bookworm"
    assert env["PD_IMAGE_ID"] == DIGEST
    # A build has no container.
    assert "PD_CONTAINER" not in env


def test_run_env_from_scratch_has_only_the_marker(tmp_path):
    env = _child_env(_stage(tmp_path))
    assert env["container"] == CONTAINER_MARKER
    assert "PD_IMAGE" not in env
    assert "PD_IMAGE_ID" not in env


def test_the_marker_wins_over_the_images_own_in_the_process_env(tmp_path):
    # Fedora and UBI ship `ENV container=oci`. That stays in the image's
    # config, which is a statement about the image; the process is told
    # what is true of the process.
    stage = _stage(tmp_path, env={"container": "oci", "LANG": "C.UTF-8"})
    env = _child_env(stage)
    assert env["container"] == CONTAINER_MARKER
    assert env["LANG"] == "C.UTF-8"
    assert stage.env["container"] == "oci"


def test_identity_is_part_of_the_recipe_hash(tmp_path):
    # A step can read $PD_IMAGE the way it reads any ENV, so two stages
    # whose base is spelled differently do not share a cached layer.
    engine = SimpleNamespace(expansion_scope=lambda: {"A": "1"})
    engine.current = _stage(tmp_path, base_ref="debian:12",
                            base_image_id=DIGEST)
    twelve = run_step._run_extra_inputs(engine)
    engine.current.base_ref = "debian:bookworm"
    bookworm = run_step._run_extra_inputs(engine)
    assert twelve != bookworm
    assert "A=1" in twelve and f"container={CONTAINER_MARKER}" in twelve
    assert f"PD_IMAGE_ID={DIGEST}" in twelve


# --- what cannot be an environment string -----------------------------------

def test_an_unexportable_env_entry_is_left_out_of_the_step(tmp_path):
    # A NUL byte in a Dockerfile's ENV line reached Popen, which raised
    # ValueError past every net in the build.
    stage = _stage(tmp_path, env={"BAD": "a\x00b", "BIG": "x" * 200_000,
                                  "OK": "1"},
                   args={"ARGBAD": "\x00"}, declared_args={"ARGBAD"})
    env = _child_env(stage)
    assert "BAD" not in env and "BIG" not in env and "ARGBAD" not in env
    assert env["OK"] == "1"


def test_a_refused_exec_is_a_build_error(tmp_path, monkeypatch):
    from proot_distro.helpers.build_engine.errors import BuildError

    stage = _stage(tmp_path)
    (tmp_path / "rootfs" / "etc").mkdir()
    monkeypatch.setattr(run_step, "IS_TERMUX", False)
    monkeypatch.setattr(run_step, "get_proot_bin", lambda: "/usr/bin/proot")
    monkeypatch.setattr(run_step, "get_device_cpu_arch", lambda: HOST_ARCH)
    monkeypatch.setattr(run_step, "get_emulator_args", lambda *a: [])
    monkeypatch.setattr(run_step, "resolve_user_for_proot",
                        lambda *a, **k: (0, 0))

    def _popen(*a, **k):
        raise ValueError("embedded null byte")

    monkeypatch.setattr(run_step.subprocess, "Popen", _popen)
    engine = SimpleNamespace(quiet=True, verbose=False, emulator="",
                             warned_host_exec=set())
    with pytest.raises(BuildError, match="cannot start proot"):
        run_step._exec_proot(engine, stage, ["true"], None)
