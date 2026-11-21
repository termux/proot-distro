# The rename that publishes a staged container cannot be aimed elsewhere.
#
# install and restore build a new container in a staging directory and
# rename it onto containers/<name> at the end (container_staging.py).
# CONTAINERS_DIR is guest-writable on Termux, so both names can change
# while the container is built: the name it lands on can be taken, and
# the staging directory itself can be swapped. rename(2) never follows
# the name it lands on and refuses anything but an empty directory, and
# the staging name is checked against the staging directory's descriptor
# before the rename and after it, so neither change has the container
# written through a link, nor publishes something this program did not
# build.

import os

import pytest

import _builders as builders
from proot_distro.constants import CONTAINERS_DIR
from proot_distro.operation_errors import (
    AlreadyExistsFailure, OperationFailure,
)
from proot_distro.operations import install as install_service
from proot_distro.operations.install import install_container
from proot_distro.paths import (
    container_dir, container_entry_lstat, container_rootfs,
    installed_container_names,
)


def _archive(path):
    builders.make_tar(str(path), builders.rootfs_members([
        {"name": "etc/hello", "type": "file", "data": b"hello"},
    ]))
    return str(path)


def _staged():
    return sorted(entry for entry in os.listdir(CONTAINERS_DIR)
                  if entry.startswith(".pd-staging-"))


def _before_commit(monkeypatch, action):
    """Run *action* once everything is built, just before the rename."""
    real = install_service.setup_fake_sysdata

    def hook(*args, **kwargs):
        real(*args, **kwargs)
        action()

    monkeypatch.setattr(install_service, "setup_fake_sysdata", hook)


def test_a_link_planted_at_the_name_is_not_followed(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    _before_commit(monkeypatch,
                   lambda: os.symlink(host, container_dir("planted")))

    with pytest.raises(AlreadyExistsFailure) as exc:
        install_container(_archive(tmp_path / "r.tar"), name="planted")

    assert "was taken while the container was being built" in str(exc.value)
    assert list(host.iterdir()) == []
    assert os.path.islink(container_dir("planted"))
    assert _staged() == []


def test_a_directory_planted_at_the_name_is_not_replaced(
        tmp_path, monkeypatch):
    def plant():
        os.mkdir(container_dir("planted"))
        with open(os.path.join(container_dir("planted"), "keep"), "w") as fh:
            fh.write("theirs")

    _before_commit(monkeypatch, plant)

    with pytest.raises(AlreadyExistsFailure):
        install_container(_archive(tmp_path / "r.tar"), name="planted")

    assert os.listdir(container_dir("planted")) == ["keep"]
    assert _staged() == []


def test_an_empty_directory_at_the_name_is_taken_over(tmp_path, monkeypatch):
    # The one thing rename(2) replaces, and nothing is lost by it.
    _before_commit(monkeypatch, lambda: os.mkdir(container_dir("empty")))

    install_container(_archive(tmp_path / "r.tar"), name="empty")

    assert installed_container_names() == ["empty"]
    assert os.path.isfile(os.path.join(container_rootfs("empty"), "etc",
                                       "hello"))


def test_a_swapped_staging_directory_is_not_published(tmp_path, monkeypatch):
    host = tmp_path / "host"
    host.mkdir()
    moved = os.path.join(CONTAINERS_DIR, ".moved-away")

    def swap():
        [staged] = _staged()
        os.rename(os.path.join(CONTAINERS_DIR, staged), moved)
        os.symlink(host, os.path.join(CONTAINERS_DIR, staged))

    _before_commit(monkeypatch, swap)

    with pytest.raises(OperationFailure) as exc:
        install_container(_archive(tmp_path / "r.tar"), name="swapped")

    assert "was replaced" in str(exc.value)
    assert container_entry_lstat("swapped") is None
    assert list(host.iterdir()) == []
    # The link standing in for the staging directory is removed, not
    # followed; what was moved away is the mover's.
    assert _staged() == []
    assert os.path.isdir(os.path.join(moved, "rootfs"))


def test_a_container_swapped_as_it_is_renamed_is_reported(
        tmp_path, monkeypatch):
    # Whatever stands under the name once the rename is done is checked
    # against what was built, so a swap in that instant is no success.
    from proot_distro import container_staging

    host = tmp_path / "host"
    host.mkdir()
    real = os.rename

    def rename_then_swap(src, dst, **kwargs):
        real(src, dst, **kwargs)
        if dst == "raced":
            real(container_dir("raced"),
                 os.path.join(CONTAINERS_DIR, ".raced-away"))
            os.symlink(host, container_dir("raced"))

    monkeypatch.setattr(container_staging.os, "rename", rename_then_swap)

    with pytest.raises(OperationFailure) as exc:
        install_container(_archive(tmp_path / "r.tar"), name="raced")

    assert "was replaced as it was installed" in str(exc.value)
    assert list(host.iterdir()) == []
