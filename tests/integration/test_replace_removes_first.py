# reset, and restore over an existing container, remove the old container
# completely before they write the new one.
#
# d92cdad had reset build the new container beside the old one and swap
# them at the end, which needed room for both: on a phone, often more than
# there is. And restore over an existing container replaced its rootfs in
# place, so one that failed or was killed left a half-replaced container
# that looked installed. Both now rename the old container aside, remove
# it, and build the new one in a staging directory renamed onto the name
# at the end (container_staging.clear_container). Peak use is the larger
# of the two containers, not their sum, and at every moment the name holds
# the complete old container, nothing, or the complete new one. Once the
# old one is gone a failure loses it, so the user is told how to get it
# back: the install command for reset, the archive again for restore.

import gzip
import io
import os
import signal
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

from proot_distro import statedir
from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA
from proot_distro.operation_errors import NotFoundFailure
from proot_distro.operations import install as install_service
from proot_distro.operations import restore as restore_service
from proot_distro.operations.backup import backup_container
from proot_distro.operations.context import OperationContext
from proot_distro.operations.install import install_container
from proot_distro.operations.reset import reset_container
from proot_distro.operations.restore import restore_container
from proot_distro.paths import (
    container_entry_lstat, container_rootfs, installed_container_names,
)

_ROOT = Path(__file__).resolve().parents[2]
ARCH = "x86_64"
OLD = b"the user's data"
HINT = (f"Container 'box' was removed, but its new copy was not installed. "
        f"Install it again with '{PROGRAM_NAME} install --name box "
        f"--architecture {ARCH} img:1'.")
RESTORE_AGAIN = ("Container 'box' was removed, but the restore did not "
                 "finish. Restore the archive again to bring it back.")


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
    }, files={"root/work": OLD})
    return "box"


@pytest.fixture
def backup(tmp_path, box):
    """A backup of 'box' holding 'backed up', with 'box' then changed."""
    work = os.path.join(container_rootfs(box), "root", "work")
    with open(work, "wb") as fh:
        fh.write(b"backed up")
    path = tmp_path / "box.tar.gz"
    backup_container(box, output_path=str(path))
    with open(work, "wb") as fh:
        fh.write(OLD)
    return str(path)


def _old_data_anywhere():
    """Whether the old container's data is anywhere in CONTAINERS_DIR."""
    for top, _dirs, files in os.walk(CONTAINERS_DIR):
        for name in files:
            try:
                with open(os.path.join(top, name), "rb") as fh:
                    if fh.read(len(OLD) + 1) == OLD:
                        return True
            except OSError:
                pass
    return False


def _content(rel):
    with open(os.path.join(container_rootfs("box"), rel), "rb") as fh:
        return fh.read()


def _staged():
    return sorted(entry for entry in os.listdir(CONTAINERS_DIR)
                  if entry.startswith(".pd-staging-"))


def _context():
    messages = []
    return OperationContext(
        emit=lambda _kind, text, _phase, _res: messages.append(text)
    ), messages


def _killed_at(hook_module, hook_name, call):
    """Run *call* in a process stopped, then killed, in *hook_name*."""
    helper = subprocess.Popen([sys.executable, "-c", f"""
import sys, time
sys.path.insert(0, {str(_ROOT)!r})
import {hook_module} as target
real = getattr(target, {hook_name!r})
def hold(*args, **kwargs):
    print("ready", flush=True)
    time.sleep(300)
setattr(target, {hook_name!r}, hold)
{call}
"""], stdout=subprocess.PIPE, text=True, env=os.environ.copy())
    assert helper.stdout.readline().strip() == "ready"
    helper.send_signal(signal.SIGKILL)
    helper.wait(10)
    helper.stdout.close()


# ----- reset ----------------------------------------------------------------

def test_reset_removes_the_old_container_before_writing_the_new(
        box, monkeypatch):
    """Regression: the new container was built beside the old one."""
    real = install_service.pull_image
    seen = []

    def pull(*args, **kwargs):
        seen.append((container_entry_lstat(box), _old_data_anywhere()))
        return real(*args, **kwargs)

    monkeypatch.setattr(install_service, "pull_image", pull)
    context, messages = _context()

    reset_container(box, context=context)

    assert seen == [(None, False)]
    assert _content("etc/hostname") == b"from-image\n"
    assert not os.path.exists(os.path.join(container_rootfs(box), "root",
                                           "work"))
    assert _staged() == []
    assert "Removing the old container 'box'..." in messages


def test_a_failed_reset_says_how_to_install_it_again(box, monkeypatch):
    def unreachable(*_args, **_kwargs):
        raise RuntimeError("the registry cannot be reached")

    monkeypatch.setattr(install_service, "pull_image", unreachable)
    context, messages = _context()

    with pytest.raises(RuntimeError):
        reset_container(box, context=context)

    assert container_entry_lstat(box) is None
    assert installed_container_names() == []
    assert _staged() == []
    assert HINT in messages

    # And the command it gives does bring it back.
    monkeypatch.undo()
    install_container("img:1", name="box", architecture=ARCH)
    assert _content("etc/hostname") == b"from-image\n"


def test_a_reset_refused_before_the_removal_keeps_the_container(
        builders):
    # The recorded architecture is the container's to rewrite; one the
    # install refuses stops the reset before anything is removed, and
    # then there is nothing to tell the user to reinstall.
    from proot_distro.operations.errors import InstallValidationFailure

    builders.make_container("box", manifest={
        "image_ref": "img:1", "arch": "nonsense",
        "manifest": {"schemaVersion": 2, "layers": []},
        "image_config": {"config": {}},
    }, files={"root/work": OLD})
    context, messages = _context()

    with pytest.raises(InstallValidationFailure):
        reset_container("box", context=context)

    assert installed_container_names() == ["box"]
    assert _content("root/work") == OLD
    assert not any("was removed" in message for message in messages)


def test_ctrl_c_during_the_removal_lets_it_finish(box, monkeypatch):
    real = statedir.remove_state_tree
    calls = []

    def remove(path, **kwargs):
        calls.append(path)
        if len(calls) == 1:
            raise KeyboardInterrupt
        return real(path, **kwargs)

    monkeypatch.setattr(statedir, "remove_state_tree", remove)
    context, messages = _context()

    with pytest.raises(KeyboardInterrupt):
        reset_container(box, context=context)

    assert len(calls) == 2
    assert container_entry_lstat(box) is None
    assert not _old_data_anywhere()
    assert HINT in messages


def test_a_reset_killed_while_removing_leaves_the_name_free(box):
    _killed_at("proot_distro.statedir", "remove_state_tree",
               "from proot_distro.operations.reset import reset_container\n"
               "reset_container('box')")

    assert container_entry_lstat(box) is None
    assert installed_container_names() == []
    assert len(_staged()) == 1
    assert _old_data_anywhere()

    # The next install sweeps what the dead reset left.
    install_container("img:1", name="box", architecture=ARCH)
    assert _staged() == []
    assert not _old_data_anywhere()


def test_a_reset_killed_while_installing_leaves_no_half_container(box):
    _killed_at("proot_distro.operations.install", "setup_fake_sysdata",
               "from proot_distro.operations.reset import reset_container\n"
               "reset_container('box')")

    assert container_entry_lstat(box) is None
    assert installed_container_names() == []
    assert not _old_data_anywhere()
    assert len(_staged()) == 1


def test_a_replacement_of_a_container_removed_meanwhile_is_refused(
        tmp_path, builders):
    # reset asks whether the container is installed before it takes the
    # lock, and a `remove` can finish in between: what it then asks for
    # is a replacement of nothing, which must not install a new one.
    archive = tmp_path / "rootfs.tar"
    builders.make_tar(str(archive), builders.rootfs_members())
    with pytest.raises(NotFoundFailure):
        install_container(str(archive), name="gone", replace=True)
    assert installed_container_names() == []
    assert _staged() == []


# ----- restore over an existing container -----------------------------------

def test_restore_removes_the_old_container_before_writing_the_new(
        box, backup, monkeypatch):
    """Regression: the old rootfs was cleared in place, and a failure
    left a half-replaced container listed as installed."""
    real = restore_service._write_regular
    seen = []

    def write(*args, **kwargs):
        if not seen:
            seen.append((container_entry_lstat(box), _old_data_anywhere()))
        return real(*args, **kwargs)

    monkeypatch.setattr(restore_service, "_write_regular", write)

    restore_container(archive_path=backup)

    assert seen == [(None, False)]
    assert _content("root/work") == b"backed up"
    assert _staged() == []


def test_a_failed_restore_over_a_container_leaves_nothing_listed(
        box, backup, tmp_path):
    """Regression: a half-replaced container stayed, listed."""
    # The backup with its compressed stream cut short after the rootfs
    # has begun: the restore fails part-way.
    raw = gzip.decompress(Path(backup).read_bytes())
    padded = io.BytesIO()
    with tarfile.open(fileobj=io.BytesIO(raw)) as src, \
            tarfile.open(fileobj=padded, mode="w") as dst:
        for member in src:
            dst.addfile(member, src.extractfile(member)
                        if member.isreg() else None)
        tail = tarfile.TarInfo("box/rootfs/zz-tail")
        payload = os.urandom(1 << 20)
        tail.size = len(payload)
        dst.addfile(tail, io.BytesIO(payload))
    data = gzip.compress(padded.getvalue(), compresslevel=1)
    cut = tmp_path / "cut.tar.gz"
    cut.write_bytes(data[:len(data) - (1 << 19)])
    context, messages = _context()

    with pytest.raises(Exception):
        restore_container(archive_path=str(cut), context=context)

    assert container_entry_lstat(box) is None
    assert installed_container_names() == []
    assert _staged() == []
    assert RESTORE_AGAIN in messages

    # Restoring a good archive again brings it back.
    restore_container(archive_path=backup)
    assert _content("root/work") == b"backed up"


def test_a_restore_over_a_container_killed_leaves_no_half_container(
        box, backup):
    _killed_at("proot_distro.operations.restore", "_rootfs_is_a_directory",
               "from proot_distro.operations.restore import "
               "restore_container\n"
               f"restore_container(archive_path={backup!r})")

    assert container_entry_lstat(box) is None
    assert installed_container_names() == []
    assert len(_staged()) == 1
