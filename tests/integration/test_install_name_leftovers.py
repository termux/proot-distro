# A name that holds leftovers, not a container, is refused up front.
#
# install used to write into whatever directory stood under the name, so
# the leftovers of an interrupted removal -- a directory with no rootfs --
# became part of the new container. A new container is renamed onto its
# name at the end now (container_staging.py), and rename(2) only replaces
# an empty directory, so such a name is refused before anything is
# downloaded or extracted, with the command that clears it. An empty
# directory is still taken over. restore to such a name is refused the
# same way.

import os
from types import SimpleNamespace

import pytest

import _builders as builders
from proot_distro.commands.install import command_install
from proot_distro.commands.restore import command_restore
from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.operation_errors import AlreadyExistsFailure
from proot_distro.operations import install as install_service
from proot_distro.operations.backup import backup_container
from proot_distro.operations.containers import remove_container
from proot_distro.operations.install import install_container
from proot_distro.paths import container_dir, installed_container_names

REFUSAL = (
    "'relic' holds the leftovers of an incomplete container. Remove them "
    f"with '{PROGRAM_NAME} remove relic'."
)


def _leftovers(name="relic"):
    os.mkdir(container_dir(name))
    with open(os.path.join(container_dir(name), "manifest.json"), "w") as fh:
        fh.write("{}")


def _staged():
    return sorted(entry for entry in os.listdir(CONTAINERS_DIR)
                  if entry.startswith(".pd-staging-"))


@pytest.fixture
def archive(tmp_path, monkeypatch):
    """A rootfs archive that must not be read: the refusal comes first."""
    path = tmp_path / "rootfs.tar"
    builders.make_tar(str(path), builders.rootfs_members())

    def read(*_args, **_kwargs):
        raise AssertionError("the source was read")

    monkeypatch.setattr(install_service, "install_from_local_file", read)
    return str(path)


def test_install_refuses_leftovers_before_reading_its_source(archive):
    _leftovers()
    with pytest.raises(AlreadyExistsFailure) as exc:
        install_container(archive, name="relic")
    assert str(exc.value) == REFUSAL
    assert os.listdir(container_dir("relic")) == ["manifest.json"]
    assert _staged() == []


def test_the_cli_names_the_command_that_clears_leftovers(archive, capsys):
    _leftovers()
    with pytest.raises(SystemExit) as exc:
        command_install(SimpleNamespace(
            image_ref=archive,
            custom_container_name="relic", override_arch=None,
        ))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert REFUSAL in err
    # It is no installed container to log into or reinstall.
    assert "Start shell" not in err


def test_an_empty_directory_under_the_name_is_taken_over(tmp_path):
    os.mkdir(container_dir("relic"))
    archive = tmp_path / "rootfs.tar"
    builders.make_tar(str(archive), builders.rootfs_members())
    install_container(str(archive), name="relic")
    assert installed_container_names() == ["relic"]


def test_restore_refuses_leftovers_without_a_traceback(tmp_path, capsys):
    builders.make_container("relic")
    backup = tmp_path / "relic.tar.gz"
    backup_container("relic", output_path=str(backup))
    remove_container("relic")
    _leftovers()

    with pytest.raises(SystemExit) as exc:
        command_restore(SimpleNamespace(archive=str(backup)))

    assert exc.value.code == 1
    assert REFUSAL in capsys.readouterr().err
    assert os.listdir(container_dir("relic")) == ["manifest.json"]
    assert _staged() == []


def test_build_refuses_leftovers_under_install_as_before_building(
        tmp_path, monkeypatch):
    # Otherwise the refusal came from the install, after the whole build.
    from proot_distro.operations import build as build_service

    _leftovers()
    context = tmp_path / "context"
    context.mkdir()
    (context / "Dockerfile").write_text("FROM scratch\n")

    def engine(*_args, **_kwargs):
        raise AssertionError("the build ran")

    monkeypatch.setattr(build_service, "BuildEngine", engine)
    with pytest.raises(AlreadyExistsFailure) as exc:
        build_service.build_image(
            str(context), tags=["leftover/app:1"], install_as="relic",
        )
    assert str(exc.value) == REFUSAL
