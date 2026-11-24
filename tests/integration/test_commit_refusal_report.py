# A refused final rename is said once, by the CLI, as what it is.
#
# install and reset build the new container in a staging directory and
# rename it onto its name at the end (container_staging.Staging.commit).
# The rename is refused when something took the name meanwhile, or
# swapped the staging directory, and says so in an AlreadyExistsFailure
# or an OperationFailure for the caller to print. install's handler took
# every RuntimeError for an install that went wrong partway: it printed
# "Failed to install: <the message>" and the line pointing at `install
# --help`, then raised the failure, which install's CLI printed again.
# reset's CLI printed the OperationFailure again, and once install no
# longer reported them, would have printed the AlreadyExistsFailure not
# at all.
#
# A reset, and a restore over a container, has removed the old one by
# then. They told the user how to get it back only when nothing at all
# was left under the name, so not when the rename was refused because
# something had taken it -- whose message says to remove what is there
# and try again, which a reset can no longer do.

import os
from types import SimpleNamespace

import pytest

from proot_distro import container_staging
from proot_distro.commands.install import command_install
from proot_distro.commands.reset import command_reset
from proot_distro.commands.restore import command_restore
from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA
from proot_distro.operations.backup import backup_container
from proot_distro.paths import container_dir, container_entry_lstat

ARCH = "x86_64"
TAKEN = "was taken while the container was being built"
SWAPPED = "was being built in was replaced; nothing was installed."
REPLACED = "was replaced as it was installed."


def _staged():
    return sorted(entry for entry in os.listdir(CONTAINERS_DIR)
                  if entry.startswith(".pd-staging-"))


def _before_commit(monkeypatch, action):
    """Run *action* once everything is built, just before the rename."""
    real = container_staging.Staging.commit

    def commit(self, target):
        action(target)
        return real(self, target)

    monkeypatch.setattr(container_staging.Staging, "commit", commit)


def _take(monkeypatch):
    """Something takes the name: rename(2) refuses it."""
    def take(name):
        os.mkdir(container_dir(name))
        with open(os.path.join(container_dir(name), "keep"), "w") as fh:
            fh.write("theirs")

    _before_commit(monkeypatch, take)


def _swap(monkeypatch):
    """The staging directory is swapped: refused before the rename."""
    def swap(_name):
        [staged] = _staged()
        os.rename(os.path.join(CONTAINERS_DIR, staged),
                  os.path.join(CONTAINERS_DIR, ".moved-away"))
        os.mkdir(os.path.join(CONTAINERS_DIR, staged))

    _before_commit(monkeypatch, swap)


def _replace(monkeypatch):
    """The container is swapped for a link as it is renamed: refused after.

    The one refusal that leaves an entry the walk will not follow.
    """
    real = os.rename

    def rename_then_swap(src, dst, **kwargs):
        real(src, dst, **kwargs)
        if not dst.startswith("."):
            real(container_dir(dst),
                 os.path.join(CONTAINERS_DIR, ".raced-away"))
            os.symlink("elsewhere", container_dir(dst))

    monkeypatch.setattr(container_staging.os, "rename", rename_then_swap)


_REFUSALS = pytest.mark.parametrize(
    "refuse,message",
    [(_take, TAKEN), (_swap, SWAPPED), (_replace, REPLACED)],
    ids=["taken", "swapped", "replaced"],
)


def _lost_as_refused(refuse, name):
    """The old container is gone; what the refusal left is not ours."""
    if refuse is _take:
        assert os.listdir(container_dir(name)) == ["keep"]
    elif refuse is _replace:
        assert os.readlink(container_dir(name)) == "elsewhere"
    else:
        assert container_entry_lstat(name) is None


def _said_once(capsys, message):
    err = capsys.readouterr().err
    assert err.count(message) == 1, err
    assert "Failed to install" not in err
    assert "install --help" not in err
    return err


@_REFUSALS
def test_install_says_a_refused_rename_once(
        builders, tmp_path, monkeypatch, capsys, refuse, message):
    archive = tmp_path / "rootfs.tar"
    builders.make_tar(str(archive), builders.rootfs_members([
        {"name": "etc/hello", "type": "file", "data": b"hello"},
    ]))
    refuse(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        command_install(SimpleNamespace(image_ref=str(archive),
                                        custom_container_name="box"))

    assert exc.value.code == 1
    _said_once(capsys, message)


@pytest.fixture
def box(builders):
    """Container 'box' of cached image img:1."""
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
    })
    return "box"


@_REFUSALS
def test_reset_says_a_refused_rename_once(
        box, monkeypatch, capsys, refuse, message):
    refuse(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        command_reset(SimpleNamespace(container_name=box))

    assert exc.value.code == 1
    err = _said_once(capsys, message)
    # The old container is gone either way, and the user is told how to
    # bring it back.
    assert (f"Install it again with '{PROGRAM_NAME} install --name box "
            f"--architecture {ARCH} img:1'.") in err
    _lost_as_refused(refuse, box)


@_REFUSALS
def test_a_restore_over_a_container_says_it_is_lost_when_refused(
        builders, tmp_path, monkeypatch, capsys, refuse, message):
    builders.make_container("box", files={"root/work": b"backed up"})
    archive = tmp_path / "box.tar.gz"
    backup_container("box", output_path=str(archive))
    refuse(monkeypatch)

    with pytest.raises(SystemExit) as exc:
        command_restore(SimpleNamespace(archive=str(archive)))

    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert err.count(message) == 1, err
    assert ("Container 'box' was removed, but the restore did not finish. "
            "Restore the archive again to bring it back.") in err
    _lost_as_refused(refuse, "box")
