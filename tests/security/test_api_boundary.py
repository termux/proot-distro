"""Security invariants exercised through the stable programmatic API.

The exploit-specific tests in this directory also exercise lower-level
services so a regression can be localized. This module deliberately invokes
attacker-controlled state only through ``proot_distro.api.ProotDistro``. It
keeps typed failures, silent operation, confinement, cleanup, and recovery at
the supported boundary under permanent test.
"""

import hashlib
import json
import os
import shutil
import subprocess

import pytest

from proot_distro.api import (
    IntegrityError,
    NotFoundError,
    OperationError,
    ProotDistro,
    RuntimeConfig,
)
from proot_distro.arch import get_device_cpu_arch
from proot_distro.constants import LAYER_CACHE_DIR, MANIFEST_CACHE_DIR
from proot_distro.helpers.docker.cache import (
    layer_cache_path,
    manifest_cache_path,
    save_manifest_cache,
)
from proot_distro.helpers.docker.refs import ARCH_TO_DOCKER
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA, canonical_json
from proot_distro.paths import container_dir, container_manifest, container_rootfs


def _assert_silent(capsys):
    assert capsys.readouterr() == ("", "")


def _rootfs_archive(tmp_path, builders, name="rootfs.tar"):
    archive = tmp_path / name
    builders.make_tar(
        str(archive),
        builders.rootfs_members([
            {"name": "etc/api-boundary", "type": "file", "data": b"safe\n"},
        ]),
    )
    return archive


def _seed_cached_image(builders, reference="boundary:1"):
    architecture = get_device_cpu_arch()
    digest, size, diff_id = builders.seed_cached_layer([
        {"name": "etc/safe", "type": "file", "data": b"cached\n"},
    ])
    config = {
        "architecture": ARCH_TO_DOCKER.get(
            architecture, (architecture, "")
        )[0],
        "os": "linux",
        "config": {},
        "rootfs": {"type": "layers", "diff_ids": [diff_id]},
    }
    config_bytes = canonical_json(config)
    manifest = {
        "schemaVersion": 2,
        "config": {
            "digest": "sha256:" + hashlib.sha256(config_bytes).hexdigest(),
            "size": len(config_bytes),
        },
        "layers": [{
            "digest": digest,
            "size": size,
            "mediaType": OCI_LAYER_MEDIA,
        }],
    }
    save_manifest_cache(
        reference, architecture, manifest, reference.rsplit(":", 1)[0], config
    )
    return architecture, digest, manifest_cache_path(reference, architecture)


def _fake_proot(tmp_path):
    path = tmp_path / "fake-proot"
    path.write_text(
        "#!/bin/sh\n"
        "marker=missing\n"
        "IFS= read -r marker < ./I_AM_REAL\n"
        "printf '%s\\n' \"$marker\"\n"
    )
    path.chmod(0o755)
    return path


def _program(proot_path):
    default = RuntimeConfig.default()
    return ProotDistro(RuntimeConfig(
        default.runtime_dir, default.cache_dir, proot_path
    ))


def test_transfer_traversal_is_typed_confined_and_recoverable(
    tmp_path, builders, capsys,
):
    builders.make_container("box")
    source = tmp_path / "payload"
    source.write_text("payload")
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("host data")
    pd = ProotDistro()

    with pytest.raises(OperationError) as exc:
        pd.copy(source, "box:../../outside/overwritten")

    assert exc.value.operation == "copy"
    assert "escapes the container directory" in str(exc.value)
    assert sentinel.read_text() == "host data"
    assert not (outside / "overwritten").exists()

    result = pd.copy(source, "box:/recovered")
    assert result.operation == "copy"
    assert open(os.path.join(container_rootfs("box"), "recovered")).read() == \
        "payload"
    _assert_silent(capsys)


def test_transfer_symlinks_cannot_write_or_read_host_files(
    tmp_path, builders, capsys,
):
    builders.make_container("box")
    rootfs = container_rootfs("box")
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret"
    secret.write_text("host secret")
    os.symlink(str(outside), os.path.join(rootfs, "escape"))
    payload = tmp_path / "payload"
    payload.write_text("guest data")
    pd = ProotDistro()

    pd.copy(payload, "box:/escape/owned")

    assert secret.read_text() == "host secret"
    assert not (outside / "owned").exists()
    reanchored = os.path.join(rootfs, str(outside).lstrip(os.sep), "owned")
    assert open(reanchored).read() == "guest data"

    stolen = tmp_path / "stolen"
    with pytest.raises(NotFoundError) as exc:
        pd.copy("box:/escape/secret", stolen)
    assert exc.value.operation == "copy"
    assert not stolen.exists()
    assert secret.read_text() == "host secret"
    _assert_silent(capsys)


def test_install_refuses_planted_container_directory_without_host_writes(
    tmp_path, builders, capsys,
):
    archive = _rootfs_archive(tmp_path, builders)
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("host data")
    os.symlink(str(outside), container_dir("planted"))
    pd = ProotDistro()

    with pytest.raises(OperationError) as exc:
        pd.install(archive, name="planted")

    assert exc.value.operation == "install"
    assert sentinel.read_text() == "host data"
    assert sorted(os.listdir(outside)) == ["sentinel"]
    assert os.path.islink(container_dir("planted"))

    os.unlink(container_dir("planted"))
    installed = pd.install(archive, name="planted")
    assert installed.name == "planted"
    assert os.path.isfile(os.path.join(
        container_rootfs("planted"), "etc", "api-boundary"
    ))
    _assert_silent(capsys)


def test_install_hostile_oci_layer_stays_inside_container(
    tmp_path, builders, capsys,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("host data")
    escaped = tmp_path.parent / f"{tmp_path.name}-oci-escape"
    archive = tmp_path / "hostile.oci.tar"
    builders.make_oci_archive(str(archive), [[
        {"name": f"../../{escaped.name}", "type": "file", "data": b"bad"},
        {"name": "etc/safe", "type": "file", "data": b"safe"},
        {"name": "bad", "type": "hardlink",
         "linkname": "../../../etc/shadow"},
    ]], arch=get_device_cpu_arch())

    installed = ProotDistro().install(archive, name="hostile-layer")

    assert installed.name == "hostile-layer"
    assert open(os.path.join(
        container_rootfs("hostile-layer"), "etc", "safe"
    ), "rb").read() == b"safe"
    assert not os.path.exists(os.path.join(
        container_rootfs("hostile-layer"), "bad"
    ))
    assert not escaped.exists()
    assert sentinel.read_text() == "host data"
    _assert_silent(capsys)


def test_install_rejects_non_regular_oci_blob_and_cleans_partial_state(
    tmp_path, builders, capsys,
):
    base = tmp_path / "base.oci.tar"
    meta = builders.make_oci_archive(
        str(base),
        [[{"name": "etc/safe", "type": "file", "data": b"safe"}]],
        arch=get_device_cpu_arch(),
    )
    layer_hex = meta["layer_digests"][0].split(":", 1)[1]
    hostile = tmp_path / "linked-blob.oci.tar"
    builders.make_oci_archive(
        str(hostile),
        [[{"name": "etc/safe", "type": "file", "data": b"safe"}]],
        arch=get_device_cpu_arch(),
        outer_extra_members=[{
            "name": "blobs/sha256/" + layer_hex,
            "type": "symlink",
            "linkname": "index.json",
        }],
    )
    pd = ProotDistro()

    with pytest.raises(IntegrityError) as exc:
        pd.install(hostile, name="linked-blob")

    assert exc.value.operation == "install"
    assert not os.path.lexists(container_dir("linked-blob"))

    installed = pd.install(base, name="linked-blob")
    assert installed.name == "linked-blob"
    _assert_silent(capsys)


def test_install_repairs_poisoned_layer_cache_from_verified_archive(
    tmp_path, builders, capsys,
):
    archive = tmp_path / "verified.oci.tar"
    meta = builders.make_oci_archive(
        str(archive),
        [[{"name": "etc/safe", "type": "file", "data": b"verified"}]],
        arch=get_device_cpu_arch(),
    )
    digest = meta["layer_digests"][0]
    path = layer_cache_path(digest)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as stream:
        stream.write(b"poisoned cache data")

    installed = ProotDistro().install(archive, name="cache-repair")

    assert installed.name == "cache-repair"
    assert open(os.path.join(
        container_rootfs("cache-repair"), "etc", "safe"
    ), "rb").read() == b"verified"
    blob = open(path, "rb").read()
    assert "sha256:" + hashlib.sha256(blob).hexdigest() == digest
    _assert_silent(capsys)


def test_symlinked_manifest_cache_is_not_read_or_swept(
    tmp_path, builders, capsys,
):
    architecture, digest, _manifest_path = _seed_cached_image(builders)
    host_cache = tmp_path / "host-manifests"
    shutil.move(MANIFEST_CACHE_DIR, host_cache)
    sentinel = host_cache / "host-sentinel"
    sentinel.write_text("host data")
    os.symlink(str(host_cache), MANIFEST_CACHE_DIR)
    pd = ProotDistro()

    assert pd.list_images() == ()
    with pytest.raises(IntegrityError) as exc:
        pd.clear_cache(orphan=True)

    assert exc.value.operation == "clear_cache"
    assert sentinel.read_text() == "host data"
    assert os.path.isfile(layer_cache_path(digest))

    os.unlink(MANIFEST_CACHE_DIR)
    shutil.move(host_cache, MANIFEST_CACHE_DIR)
    assert [image.reference for image in pd.list_images()] == ["boundary:1"]
    assert architecture == get_device_cpu_arch()
    _assert_silent(capsys)


def test_poisoned_manifest_cannot_trigger_destructive_orphan_sweep(
    builders, capsys,
):
    _architecture, digest, path = _seed_cached_image(builders)
    valid = open(path, "rb").read()
    with open(path, "wb") as stream:
        stream.write(b'{"manifest":')
    pd = ProotDistro()

    with pytest.raises(IntegrityError) as exc:
        pd.clear_cache(orphan=True)

    assert exc.value.operation == "clear_cache"
    assert os.path.isfile(layer_cache_path(digest))
    assert open(layer_cache_path(digest), "rb").read()

    with open(path, "wb") as stream:
        stream.write(valid)
    result = pd.clear_cache(orphan=True)
    assert result.mode == "orphan"
    assert os.path.isfile(layer_cache_path(digest))
    _assert_silent(capsys)


def test_restore_hostile_members_are_contained_through_api(
    tmp_path, builders, capsys,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("host data")
    escaped = tmp_path.parent / f"{tmp_path.name}-restore-escape"
    archive = tmp_path / "hostile-backup.tar"
    builders.make_tar(str(archive), [
        {"name": f"../{escaped.name}", "type": "file", "data": b"bad"},
        {"name": "restored/rootfs/etc/safe", "type": "file",
         "data": b"safe"},
        {"name": "restored/rootfs/bad", "type": "hardlink",
         "linkname": "../../../../etc/shadow"},
        {"name": "/restored/absolute", "type": "file", "data": b"inside"},
    ])

    result = ProotDistro().restore(archive)

    assert result.container.name == "restored"
    assert open(os.path.join(
        container_rootfs("restored"), "etc", "safe"
    ), "rb").read() == b"safe"
    assert not os.path.exists(os.path.join(container_rootfs("restored"), "bad"))
    assert open(os.path.join(
        container_rootfs("restored"), "absolute"
    ), "rb").read() == b"inside"
    assert sentinel.read_text() == "host data"
    assert not escaped.exists()
    _assert_silent(capsys)


def test_restore_rejects_multiple_containers_without_touching_second_target(
    tmp_path, builders, capsys,
):
    hostile = tmp_path / "multiple.tar"
    builders.make_tar(str(hostile), [
        {"name": "first/rootfs/etc/safe", "type": "file", "data": b"one"},
        {"name": "second/rootfs/etc/safe", "type": "file", "data": b"two"},
    ])
    pd = ProotDistro()

    with pytest.raises(IntegrityError) as exc:
        pd.restore(hostile)

    assert exc.value.operation == "restore"
    assert not os.path.lexists(container_dir("second"))
    assert open(os.path.join(
        container_rootfs("first"), "etc", "safe"
    ), "rb").read() == b"one"

    # Restore is streamed and does not promise rollback after its first
    # materialized rootfs member. The committed target remains confined and
    # manageable after the typed late failure.
    removed = pd.remove_container("first")
    assert removed.name == "first"
    assert not os.path.lexists(container_dir("first"))

    safe = tmp_path / "safe.tar"
    builders.make_tar(str(safe), [
        {"name": "recovered/rootfs/etc/safe", "type": "file",
         "data": b"safe"},
    ])
    assert pd.restore(safe).container.name == "recovered"
    _assert_silent(capsys)


def test_restore_refuses_planted_container_directory_without_host_writes(
    tmp_path, builders, capsys,
):
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("host data")
    os.symlink(str(outside), container_dir("planted-restore"))
    archive = tmp_path / "planted.tar"
    builders.make_tar(str(archive), [
        {"name": "planted-restore/rootfs/etc/safe", "type": "file",
         "data": b"safe"},
    ])

    with pytest.raises(OperationError) as exc:
        ProotDistro().restore(archive)

    assert exc.value.operation == "restore"
    assert sentinel.read_text() == "host data"
    assert sorted(os.listdir(outside)) == ["sentinel"]
    assert os.path.islink(container_dir("planted-restore"))
    _assert_silent(capsys)


def test_run_rejects_symlinked_manifest_without_using_host_configuration(
    tmp_path, builders, capsys,
):
    builders.make_container("manifest-box", arch=get_device_cpu_arch())
    host_manifest = tmp_path / "host-manifest.json"
    host_manifest.write_text(json.dumps({
        "image_config": {"config": {
            "Entrypoint": ["/bin/host-command"],
            "Cmd": ["host-argument"],
        }},
    }))
    os.symlink(str(host_manifest), container_manifest("manifest-box"))
    fake_proot = _fake_proot(tmp_path)
    pd = _program(fake_proot)

    with pytest.raises(OperationError) as exc:
        pd.run(
            "manifest-box", stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

    assert exc.value.operation == "run"
    assert "/bin/host-command" not in str(exc.value)
    assert pd.list_sessions() == ()
    assert host_manifest.is_file()
    _assert_silent(capsys)


def test_session_refuses_planted_rootfs_and_recovers(
    tmp_path, builders, capsys,
):
    builders.make_container(
        "session-box", arch=get_device_cpu_arch(), files={"I_AM_REAL": b"real\n"}
    )
    real = container_rootfs("session-box") + ".real"
    os.rename(container_rootfs("session-box"), real)
    outside = tmp_path / "outside-rootfs"
    outside.mkdir()
    sentinel = outside / "sentinel"
    sentinel.write_text("host data")
    os.symlink(str(outside), container_rootfs("session-box"))
    pd = _program(_fake_proot(tmp_path))

    with pytest.raises(NotFoundError) as exc:
        pd.start(
            "session-box", stdout=subprocess.PIPE, stderr=subprocess.PIPE
        )

    assert exc.value.operation == "start"
    assert sentinel.read_text() == "host data"
    assert pd.list_sessions() == ()

    os.unlink(container_rootfs("session-box"))
    os.rename(real, container_rootfs("session-box"))
    process = pd.start(
        "session-box", stdout=subprocess.PIPE, stderr=subprocess.PIPE
    )
    stdout, stderr = process.communicate(timeout=10)
    assert stdout == b"real\n"
    assert stderr == b""
    assert pd.list_sessions() == ()
    _assert_silent(capsys)


def test_session_uses_pinned_rootfs_after_atomic_container_replacement(
    tmp_path, builders, capsys,
):
    builders.make_container(
        "session-box", arch=get_device_cpu_arch(), files={"I_AM_REAL": b"real\n"}
    )
    builders.make_container(
        "standin", arch=get_device_cpu_arch(), files={"I_AM_REAL": b"decoy\n"}
    )
    first = tmp_path / "first-bind"
    second = tmp_path / "second-bind"
    first.mkdir()
    second.mkdir()
    moved = container_dir("session-box") + ".moved"
    swapped = False

    def swap_on_setup_warning(event):
        nonlocal swapped
        if swapped or event.operation != "start":
            return
        if event.phase == "session-setup" and "overlaps" in event.message:
            os.rename(container_dir("session-box"), moved)
            os.symlink(container_dir("standin"), container_dir("session-box"))
            swapped = True

    default = RuntimeConfig.default()
    pd = ProotDistro(RuntimeConfig(
        default.runtime_dir, default.cache_dir, _fake_proot(tmp_path)
    ), event_handler=swap_on_setup_warning)
    try:
        process = pd.start(
            "session-box",
            binds=[f"{first}:/mnt", f"{second}:/mnt"],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = process.communicate(timeout=10)
    finally:
        if os.path.islink(container_dir("session-box")):
            os.unlink(container_dir("session-box"))
        if os.path.isdir(moved):
            os.rename(moved, container_dir("session-box"))

    assert swapped
    assert stdout == b"real\n"
    assert stderr == b""
    assert pd.list_sessions() == ()
    _assert_silent(capsys)
