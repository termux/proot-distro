# An install or a restore killed outright leaves no container behind.
#
# Both used to write the new container in place, under containers/<name>,
# and remove it again if they failed. A process killed outright -- the
# API's SIGKILL once its grace period is up, a second Ctrl-C there,
# Android reclaiming Termux -- runs no cleanup, so the half-written tree
# stayed: `list` showed it, `login` entered it, and the next install of
# the name was refused. A new container is built in a staging directory
# now and renamed onto its name only once complete (container_staging.py).
# Killed, it leaves only that directory, which is no container and which
# the next install, restore or clear-cache sweeps.
#
# The processes are killed with SIGKILL at the last moment there is:
# everything written, nothing renamed.

import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest

import _builders as builders
from proot_distro.constants import CONTAINERS_DIR
from proot_distro.container_staging import reclaim_staging
from proot_distro.operations.backup import backup_container
from proot_distro.operations.cache import clear_cache
from proot_distro.operations.containers import remove_container
from proot_distro.operations.context import OperationContext
from proot_distro.operations.install import install_container
from proot_distro.operations.restore import restore_container
from proot_distro.paths import (
    container_entry_lstat, container_rootfs, installed_container_names,
)

_ROOT = Path(__file__).resolve().parents[2]

# Runs install or restore and stops it, alive, once everything is
# written: before the rename the staged design ends with, and after the
# last write the in-place one made.
_HELPER = """
import sys, time
sys.path.insert(0, {root!r})
from proot_distro.operations import install as install_service
from proot_distro.operations import restore as restore_service

def ready():
    print("ready", flush=True)
    time.sleep(300)

if {kind!r} == "install":
    real = install_service.setup_fake_sysdata
    def hold(*args, **kwargs):
        real(*args, **kwargs)
        ready()
    install_service.setup_fake_sysdata = hold
    install_service.install_container({source!r}, name={name!r})
else:
    real = restore_service._rootfs_is_a_directory
    def hold(*args, **kwargs):
        answer = real(*args, **kwargs)
        ready()
        return answer
    restore_service._rootfs_is_a_directory = hold
    restore_service.restore_container(archive_path={source!r})
"""


def _archive(path):
    builders.make_tar(str(path), builders.rootfs_members([
        {"name": "etc/hello", "type": "file", "data": b"hello"},
    ]))
    return str(path)


def _backup(path, name):
    builders.make_container(name)
    backup_container(name, output_path=str(path))
    remove_container(name)
    return str(path)


def _start(kind, source, name=""):
    helper = subprocess.Popen(
        [sys.executable, "-c", _HELPER.format(
            root=str(_ROOT), kind=kind, source=source, name=name)],
        stdout=subprocess.PIPE, text=True, env=os.environ.copy(),
    )
    assert helper.stdout.readline().strip() == "ready"
    return helper


def _kill(helper):
    helper.send_signal(signal.SIGKILL)
    helper.wait(10)
    helper.stdout.close()


def _staged():
    return sorted(entry for entry in os.listdir(CONTAINERS_DIR)
                  if entry.startswith(".pd-staging-"))


def _context():
    messages = []
    return OperationContext(
        emit=lambda _kind, text, _phase, _res: messages.append(text)
    ), messages


@pytest.mark.parametrize("kind", ["install", "restore"])
def test_a_killed_process_leaves_no_container(tmp_path, kind):
    """Regression: the half-written container stayed, listed."""
    if kind == "install":
        source = _archive(tmp_path / "rootfs.tar")
    else:
        source = _backup(tmp_path / "backup.tar.gz", "killed")
    _kill(_start(kind, source, "killed"))

    assert container_entry_lstat("killed") is None
    assert installed_container_names() == []
    # Everything it wrote is in the one staging directory it left.
    [staged] = _staged()
    assert os.path.isdir(os.path.join(CONTAINERS_DIR, staged, "rootfs"))


def test_the_next_install_sweeps_what_a_killed_one_left(tmp_path):
    archive = _archive(tmp_path / "rootfs.tar")
    _kill(_start("install", archive, "killed"))
    context, messages = _context()

    install_container(archive, name="killed", context=context)

    assert installed_container_names() == ["killed"]
    with open(os.path.join(container_rootfs("killed"), "etc", "hello"),
              "rb") as fh:
        assert fh.read() == b"hello"
    assert _staged() == []
    assert ("Removing what 1 interrupted install(s) or restore(s) left..."
            in messages)


def test_the_next_restore_sweeps_what_a_killed_install_left(tmp_path):
    archive = _archive(tmp_path / "rootfs.tar")
    backup = _backup(tmp_path / "backup.tar.gz", "restored")
    _kill(_start("install", archive, "killed"))

    restore_container(archive_path=backup)

    assert installed_container_names() == ["restored"]
    assert _staged() == []


def test_clear_cache_sweeps_what_a_killed_restore_left(tmp_path):
    backup = _backup(tmp_path / "backup.tar.gz", "killed")
    _kill(_start("restore", backup))
    context, messages = _context()

    clear_cache(context=context)

    assert _staged() == []
    assert ("Removing what 1 interrupted install(s) or restore(s) left..."
            in messages)


def test_a_running_install_keeps_its_staging_directory(tmp_path):
    # Alive and holding the lock: no sweep takes what it is building.
    helper = _start("install", _archive(tmp_path / "rootfs.tar"), "alive")
    try:
        [staged] = _staged()
        assert reclaim_staging() == 0
        clear_cache()
        assert _staged() == [staged]
    finally:
        _kill(helper)
    assert reclaim_staging() == 1
