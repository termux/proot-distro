import os
import threading
import time

import pytest

from proot_distro.api import (
    EventKind,
    IntegrityError,
    NotFoundError,
    OperationCancelledError,
    ProotDistro,
)
from proot_distro.arch import get_device_cpu_arch
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA
from proot_distro.paths import container_dir, container_rootfs


def _large_rootfs_archive(tmp_path, builders, name="large-rootfs.tar"):
    archive = tmp_path / name
    members = [
        {
            "name": f"usr/share/api-data/item-{index:04d}",
            "type": "file",
            "data": b"x" * 4096,
        }
        for index in range(1500)
    ]
    members.insert(0, {"name": "usr/share/api-data/", "type": "dir"})
    builders.make_tar(
        str(archive), builders.rootfs_members(members)
    )
    return archive


def test_keyboard_interrupt_during_install_cleans_partial_container(
        tmp_path, builders):
    archive = _large_rootfs_archive(tmp_path, builders)
    interrupted = []

    def interrupt(event):
        if (event.operation == "install"
                and event.kind == EventKind.INFO
                and event.message.startswith("Extracting rootfs")):
            interrupted.append(event)
            raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        ProotDistro(event_handler=interrupt).install(
            archive, name="interrupted-install"
        )

    assert interrupted
    assert not os.path.lexists(container_dir("interrupted-install"))
    assert ProotDistro().list_containers() == ()


def test_cancellation_during_install_leaves_recoverable_state(
        tmp_path, builders):
    archive = _large_rootfs_archive(
        tmp_path, builders, "cancelled-rootfs.tar"
    )
    cancelled = threading.Event()
    progress_seen = []

    def cancel_during_extract(event):
        if (event.operation == "install"
                and event.kind == EventKind.PROGRESS
                and not progress_seen):
            progress_seen.append(event)
            cancelled.set()

    with pytest.raises(OperationCancelledError) as exc:
        ProotDistro(event_handler=cancel_during_extract).install(
            archive, name="cancelled-install", cancel_event=cancelled
        )

    assert exc.value.operation == "install"
    assert progress_seen

    # Cancellation is not a rollback guarantee, so accept either a cleaned
    # staging tree or a committed container and prove both outcomes can be
    # recovered through the public API. (A cancel that still let the
    # install finish was a Ctrl-C lost inside tarfile -- see
    # tests/unit/test_interrupt_delivery.py -- not the event pipe.)
    cleanup = ProotDistro()
    if os.path.lexists(container_dir("cancelled-install")):
        cleanup.remove_container("cancelled-install")
    installed = cleanup.install(archive, name="cancelled-install")

    assert installed.name == "cancelled-install"
    assert os.path.isfile(os.path.join(
        container_rootfs("cancelled-install"),
        "usr", "share", "api-data", "item-1499",
    ))


def _restore_archive(tmp_path, builders):
    archive = tmp_path / "recoverable-restore.tar"
    members = [
        {
            "name": "recoverable/rootfs/first",
            "type": "file",
            "data": b"first",
        },
    ]
    members.extend({
        "name": f"recoverable/rootfs/data/item-{index:04d}",
        "type": "file",
        "data": b"y" * 4096,
    } for index in range(1500))
    members.append({
        "name": "recoverable/rootfs/last",
        "type": "file",
        "data": b"last",
    })
    builders.make_tar(str(archive), members)
    return archive


def test_cancellation_after_restore_commit_is_recoverable(
        tmp_path, builders):
    old_files = {
        f"old-data/item-{index:04d}": b"old" for index in range(500)
    }
    old_files["old"] = b"old"
    builders.make_container("recoverable", files=old_files)
    archive = _restore_archive(tmp_path, builders)
    cancelled = threading.Event()
    commit_seen = []

    def cancel_after_commit(event):
        if event.operation == "restore" and event.kind == EventKind.PROGRESS:
            commit_seen.append(event)
            if len(commit_seen) == 50:
                cancelled.set()

    pd = ProotDistro(event_handler=cancel_after_commit)
    with pytest.raises(OperationCancelledError) as exc:
        pd.restore(archive, cancel_event=cancelled)

    assert exc.value.operation == "restore"
    assert len(commit_seen) >= 50
    old_data = os.path.join(container_rootfs("recoverable"), "old-data")
    remaining = len(os.listdir(old_data)) if os.path.isdir(old_data) else 0
    assert remaining < 500

    result = ProotDistro().restore(archive)

    assert result.container.name == "recoverable"
    assert open(os.path.join(
        container_rootfs("recoverable"), "first"
    ), "rb").read() == b"first"
    assert open(os.path.join(
        container_rootfs("recoverable"), "last"
    ), "rb").read() == b"last"


def _cached_reset_image(builders):
    architecture = get_device_cpu_arch()
    entries = [{
        "name": f"var/lib/reset-data/item-{index:04d}",
        "type": "file",
        "data": b"z" * 4096,
    } for index in range(1500)]
    entries.insert(0, {"name": "var/lib/reset-data/", "type": "dir"})
    digest, size, diff_id = builders.seed_cached_layer(entries)
    manifest = {
        "schemaVersion": 2,
        "layers": [{
            "digest": digest,
            "size": size,
            "mediaType": OCI_LAYER_MEDIA,
        }],
    }
    image_config = {"config": {}, "rootfs": {"diff_ids": [diff_id]}}
    save_manifest_cache(
        "cancel-reset:1",
        architecture,
        manifest,
        "library/cancel-reset",
        image_config,
    )
    return architecture, manifest, image_config


def test_cancellation_during_reset_leaves_state_reinstallable(builders):
    architecture, manifest, image_config = _cached_reset_image(builders)
    builders.make_container("cancel-reset", manifest={
        "image_ref": "cancel-reset:1",
        "arch": architecture,
        "manifest": manifest,
        "image_config": image_config,
    }, files={"old": b"old"})
    cancelled = threading.Event()
    progress_seen = []

    def cancel_during_extract(event):
        if (event.operation == "reset_container"
                and event.kind == EventKind.PROGRESS
                and not progress_seen):
            progress_seen.append(event)
            cancelled.set()

    pd = ProotDistro(event_handler=cancel_during_extract)
    with pytest.raises(OperationCancelledError) as exc:
        pd.reset_container("cancel-reset", cancel_event=cancelled)

    assert exc.value.operation == "reset_container"
    assert progress_seen

    cleanup = ProotDistro()
    try:
        cleanup.remove_container("cancel-reset")
    except NotFoundError:
        pass
    restored = cleanup.install("cancel-reset:1", name="cancel-reset")

    assert restored.name == "cancel-reset"
    assert os.path.isfile(os.path.join(
        container_rootfs("cancel-reset"),
        "var", "lib", "reset-data", "item-1499",
    ))


def test_cancellation_during_remove_can_be_retried(builders):
    builders.make_container("cancel-remove")
    rootfs = container_rootfs("cancel-remove")
    data = os.path.join(rootfs, "remove-data")
    os.mkdir(data)
    for index in range(12000):
        with open(os.path.join(data, f"item-{index:05d}"), "wb"):
            pass

    cancelled = threading.Event()
    remove_started = []

    def cancel_after_removal_starts(event):
        if (event.operation == "remove_container"
                and event.phase == "remove"
                and not remove_started):
            remove_started.append(event)
            cancelled.set()
            time.sleep(0.02)

    pd = ProotDistro(event_handler=cancel_after_removal_starts)
    with pytest.raises(OperationCancelledError) as exc:
        pd.remove_container("cancel-remove", cancel_event=cancelled)

    assert exc.value.operation == "remove_container"
    assert remove_started

    cleanup = ProotDistro()
    try:
        cleanup.remove_container("cancel-remove")
    except NotFoundError:
        pass
    assert not os.path.lexists(container_dir("cancel-remove"))


def _bulky_container(builders, name):
    builders.make_container(name, files={
        f"data/item-{index:04d}": b"z" * 4096 for index in range(3000)
    })


def test_cancellation_during_backup_leaves_no_archive(tmp_path, builders):
    _bulky_container(builders, "bulky")
    output = tmp_path / "out" / "bulky.tar.gz"
    output.parent.mkdir()
    cancelled = threading.Event()

    def cancel_when_archiving(event):
        if event.operation == "backup" and event.kind == EventKind.PROGRESS:
            cancelled.set()

    pd = ProotDistro(event_handler=cancel_when_archiving)
    with pytest.raises(OperationCancelledError) as exc:
        pd.backup("bulky", output, cancel_event=cancelled)

    assert exc.value.operation == "backup"
    # Path outputs are published atomically: neither the archive nor a
    # half-written temporary file may remain next to it.
    assert os.listdir(output.parent) == []
    # The container is unlocked and untouched, so a retry succeeds.
    assert ProotDistro().backup("bulky", output).output == str(output)
    assert output.stat().st_size > 0


def test_cancelled_restore_removes_the_container_it_created(
        tmp_path, builders):
    archive = _restore_archive(tmp_path, builders)
    cancelled = threading.Event()
    seen = []

    def cancel_mid_extract(event):
        if event.operation == "restore" and event.kind == EventKind.PROGRESS:
            seen.append(event)
            if len(seen) == 50:
                cancelled.set()

    with pytest.raises(OperationCancelledError) as exc:
        ProotDistro(event_handler=cancel_mid_extract).restore(
            archive, cancel_event=cancelled
        )

    assert exc.value.operation == "restore"
    # The restore created this container, so there is nothing to go back
    # to: no half-extracted, manifest-less container may be left listed.
    assert not os.path.lexists(container_dir("recoverable"))
    assert ProotDistro().list_containers() == ()
    assert ProotDistro().restore(archive).container.name == "recoverable"


def test_cancellation_during_build_leaves_no_image(tmp_path):
    context = tmp_path / "context"
    (context / "data").mkdir(parents=True)
    for index in range(1500):
        (context / "data" / f"item-{index:04d}").write_bytes(b"q" * 4096)
    (context / "Dockerfile").write_text("FROM scratch\nCOPY data /data\n")
    cancelled = threading.Event()

    def cancel_on_first_event(event):
        if event.operation == "build":
            cancelled.set()

    with pytest.raises(OperationCancelledError) as exc:
        ProotDistro(event_handler=cancel_on_first_event).build(
            context, tags=["cancelled/build:1"], cancel_event=cancelled
        )

    assert exc.value.operation == "build"
    assert ProotDistro().list_images() == ()
    # The build lock was released and nothing half-built is reusable.
    built = ProotDistro().build(context, tags=["cancelled/build:1"])
    assert [image.reference for image in built.images] == [
        "cancelled/build:1"
    ]


def test_failed_restore_removes_the_container_it_created(
        tmp_path, builders):
    """Regression: a truncated archive left a manifest-less container."""
    archive = _restore_archive(tmp_path, builders)
    data = archive.read_bytes()
    truncated = tmp_path / "truncated.tar"
    truncated.write_bytes(data[:len(data) // 2])

    with pytest.raises(IntegrityError):
        ProotDistro().restore(truncated)

    assert not os.path.lexists(container_dir("recoverable"))
    assert ProotDistro().list_containers() == ()
    assert ProotDistro().restore(archive).container.name == "recoverable"


def test_failed_restore_over_an_existing_container_can_be_run_again(
        tmp_path, builders):
    """A restore over a container removes it first; a failure leaves none.

    The old container used to be overwritten in place, so a failed
    restore left it half-replaced, listed as installed. It is removed
    completely before the new one is written now, so the two never take
    up space at once (container_staging.clear_container): a failure
    leaves nothing under the name, and the archive brings it back.
    """
    builders.make_container("recoverable", files={"old": b"old"})
    archive = _restore_archive(tmp_path, builders)
    data = archive.read_bytes()
    truncated = tmp_path / "truncated.tar"
    truncated.write_bytes(data[:len(data) // 2])

    with pytest.raises(IntegrityError):
        ProotDistro().restore(truncated)

    assert not os.path.lexists(container_dir("recoverable"))
    assert [c.name for c in ProotDistro().list_containers()] == []
    assert ProotDistro().restore(archive).container.name == "recoverable"
    assert os.path.isdir(container_rootfs("recoverable"))
