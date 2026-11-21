# A reset that does not finish leaves the container as it was.
#
# reset removed the container's rootfs first and then installed the image
# again into what was left, so a reset that failed (the registry
# unreachable, the image damaged), was stopped or was killed had already
# lost the container: everything in it was gone before the first byte of
# the new one arrived. It builds the new container in a staging directory
# now, with the old one where it is, and swaps the two only once the new
# one is complete (install_container(replace=True), container_staging.py).

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

from proot_distro import container_staging
from proot_distro.constants import CONTAINERS_DIR
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA
from proot_distro.operations import install as install_service
from proot_distro.operations.context import OperationContext
from proot_distro.operations.reset import reset_container
from proot_distro.paths import container_rootfs, installed_container_names

_ROOT = Path(__file__).resolve().parents[2]
ARCH = "x86_64"


@pytest.fixture
def box(builders):
    """Container 'box' of cached image img:1, holding the user's data."""
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "etc/hostname", "type": "file", "data": b"from-image\n"}]
    )
    manifest = {"schemaVersion": 2,
                "layers": [{"digest": digest, "size": size,
                            "mediaType": OCI_LAYER_MEDIA}]}
    save_manifest_cache("img:1", ARCH, manifest, "library/img",
                        {"config": {}, "rootfs": {"diff_ids": [diff_id]}})
    builders.make_container("box", manifest={
        "image_ref": "img:1", "arch": ARCH,
        "manifest": manifest, "image_config": {"config": {}},
    }, files={"root/work": b"the user's data"})
    return "box"


def _old_data_is_there():
    path = os.path.join(container_rootfs("box"), "root", "work")
    with open(path, "rb") as fh:
        return fh.read() == b"the user's data"


def _reset_is_there():
    path = os.path.join(container_rootfs("box"), "etc", "hostname")
    with open(path, "rb") as fh:
        return (fh.read() == b"from-image\n"
                and not os.path.exists(os.path.join(
                    container_rootfs("box"), "root", "work")))


def _staged():
    return sorted(entry for entry in os.listdir(CONTAINERS_DIR)
                  if entry.startswith(".pd-staging-"))


def _context():
    messages = []
    return OperationContext(
        emit=lambda _kind, text, _phase, _res: messages.append(text)
    ), messages


def test_a_failed_reset_leaves_the_container_as_it_was(box, monkeypatch):
    """Regression: the rootfs was gone before the download began."""
    def unreachable(*_args, **_kwargs):
        raise RuntimeError("the registry cannot be reached")

    monkeypatch.setattr(install_service, "pull_image", unreachable)

    with pytest.raises(RuntimeError):
        reset_container(box)

    assert installed_container_names() == ["box"]
    assert _old_data_is_there()
    assert _staged() == []


_HELPER = """
import sys, time
sys.path.insert(0, {root!r})
from proot_distro.operations import install as install_service
from proot_distro.operations.reset import reset_container
real = install_service.setup_fake_sysdata
def hold(*args, **kwargs):
    real(*args, **kwargs)
    print("ready", flush=True)
    time.sleep(300)
install_service.setup_fake_sysdata = hold
reset_container("box")
"""


def test_a_killed_reset_leaves_the_container_as_it_was(box):
    """Regression: killed outright, reset had lost the container."""
    helper = subprocess.Popen(
        [sys.executable, "-c", _HELPER.format(root=str(_ROOT))],
        stdout=subprocess.PIPE, text=True, env=os.environ.copy(),
    )
    assert helper.stdout.readline().strip() == "ready"
    helper.send_signal(signal.SIGKILL)
    helper.wait(10)
    helper.stdout.close()

    assert installed_container_names() == ["box"]
    assert _old_data_is_there()
    assert len(_staged()) == 1

    # The next reset sweeps what that one left, and goes through.
    reset_container(box)
    assert _reset_is_there()
    assert _staged() == []


def test_a_reset_replaces_the_container_and_removes_the_old_one(box):
    context, messages = _context()

    reset_container(box, context=context)

    assert _reset_is_there()
    assert _staged() == []
    assert "Removing the old rootfs of 'box'..." in messages


def test_ctrl_c_between_the_two_renames_waits_for_the_second(
        box, monkeypatch):
    # The swap is two renames; an interrupt landing between them would
    # leave the name with no container at all.
    real = os.rename

    def rename_then_ctrl_c(src, dst, **kwargs):
        real(src, dst, **kwargs)
        if src == box:              # the old container moved aside
            os.kill(os.getpid(), signal.SIGINT)

    monkeypatch.setattr(container_staging.os, "rename", rename_then_ctrl_c)
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        with pytest.raises(KeyboardInterrupt):
            reset_container(box)
    finally:
        signal.signal(signal.SIGINT, previous)

    assert installed_container_names() == ["box"]
    assert _reset_is_there()


def test_a_failed_swap_puts_the_old_container_back(box, monkeypatch):
    # The renames come in this order: the old container aside, the new
    # one onto the name -- refused here -- and the old one back.
    real = os.rename
    aside = []

    def refuse_the_new_one(src, dst, **kwargs):
        if dst == box and src not in aside:
            raise OSError(5, "Input/output error")
        real(src, dst, **kwargs)
        if src == box:
            aside.append(dst)

    monkeypatch.setattr(container_staging.os, "rename", refuse_the_new_one)

    with pytest.raises(Exception):
        reset_container(box)

    assert len(aside) == 1
    assert installed_container_names() == ["box"]
    assert _old_data_is_there()
    assert _staged() == []


def test_a_replacement_of_a_container_removed_meanwhile_is_refused(
        tmp_path, builders):
    # reset asks whether the container is installed before it takes the
    # lock, and a `remove` can finish in between: what it then asks for
    # is a replacement of nothing, which must not install a new one.
    from proot_distro.operation_errors import NotFoundFailure
    from proot_distro.operations.install import install_container

    archive = tmp_path / "rootfs.tar"
    builders.make_tar(str(archive), builders.rootfs_members())
    with pytest.raises(NotFoundFailure):
        install_container(str(archive), name="gone", replace=True)
    assert installed_container_names() == []
    assert _staged() == []
