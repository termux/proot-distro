# A container that install or restore created but could not finish must
# not outlive the failure.
#
# Such a tree used to be written in place, under containers/<name>, and
# until it was removed it looked installed: it was listed, it could be
# logged into, and the next install of the name was refused. The removal
# comes right after the failure is reported. That is where the API's stop
# lands when an event handler raises on the report, and where a late
# cancel or Ctrl-C lands too. Install removed the container in each
# failure handler, after the report, so an interrupt during the report
# skipped the removal. Restore removed it in a finally, so an interrupt in
# the removal cut it short. Either way the half-written container stayed.
#
# Both build a new container in a staging directory now and rename it
# onto its name only once it is complete (container_staging.py), so
# nothing is ever at the name until then. What a stopped or failed
# removal leaves is the staging directory: no container, and swept by
# the next install, restore or clear-cache once its process is gone.
# tests/integration/test_killed_install.py kills the process outright.

import fcntl
import gzip
import io
import os
import tarfile

import pytest

from _builders import make_container
from proot_distro import build_scratch, statedir
from proot_distro.api import ProotDistro
from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.container_staging import reclaim_staging
from proot_distro.locking import container_lock_path
from proot_distro.operations import install as install_service
from proot_distro.operations import restore as restore_service
from proot_distro.operations.backup import backup_container
from proot_distro.operations.containers import remove_container
from proot_distro.operations.context import OperationContext
from proot_distro.operations.install import install_container
from proot_distro.operations.restore import restore_container
from proot_distro.paths import container_entry_lstat, container_rootfs

FILES = 1000
TAIL = 1 << 20


class _HandlerFailed(Exception):
    pass


def _cut(path, data):
    """Write *data* without the second half of its last member."""
    path.write_bytes(data[:len(data) - TAIL // 2])


def _rootfs_archive(path, *, truncated=True):
    """A rootfs tarball that fails at its very end when *truncated*."""
    raw = io.BytesIO()
    with tarfile.open(fileobj=raw, mode="w") as tf:
        for name in ("etc", "many"):
            info = tarfile.TarInfo(name)
            info.type = tarfile.DIRTYPE
            info.mode = 0o755
            tf.addfile(info)
        payloads = [(f"many/f{i:05d}", b"x" * 64) for i in range(FILES)]
        payloads.append(("many/zz-tail", os.urandom(TAIL)))
        for name, data in payloads:
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
    data = gzip.compress(raw.getvalue(), compresslevel=1)
    if truncated:
        _cut(path, data)
    else:
        path.write_bytes(data)
    return path


def _backup_archive(path, name, *, truncated=True):
    """A backup of a fresh container *name*, which is then removed."""
    make_container(name)
    many = os.path.join(container_rootfs(name), "many")
    os.makedirs(many)
    for i in range(FILES):
        with open(os.path.join(many, f"f{i:05d}"), "wb") as fh:
            fh.write(b"x" * 64)
    with open(os.path.join(many, "zz-tail"), "wb") as fh:
        fh.write(os.urandom(TAIL))
    backup_container(name, output_path=str(path))
    remove_container(name)
    if truncated:
        _cut(path, path.read_bytes())
    return path


def _context(interrupt_on=None):
    """An OperationContext recording messages; Ctrl-C on *interrupt_on*."""
    messages = []

    def emit(_kind, message, _phase, _resource):
        messages.append(message)
        if interrupt_on is not None and message.startswith(interrupt_on):
            raise KeyboardInterrupt

    return OperationContext(emit=emit), messages


def _interrupt_removal(monkeypatch, times, *, removes=True):
    """Raise KeyboardInterrupt from the first *times* tree removals.

    With *removes* False, the removals after those leave the tree as it
    is and report failure, as one that cannot remove everything does.
    """
    real = statedir.remove_state_tree
    calls = []

    def remove(path, **kwargs):
        calls.append(path)
        if len(calls) <= times:
            raise KeyboardInterrupt
        if not removes:
            return False
        return real(path, **kwargs)

    monkeypatch.setattr(statedir, "remove_state_tree", remove)
    return calls


def _staged():
    """The staging directories in CONTAINERS_DIR, by name."""
    return sorted(entry for entry in os.listdir(CONTAINERS_DIR)
                  if entry.startswith(".pd-staging-"))


def _left_message(staged):
    return (
        f"What the unfinished container left in "
        f"'{os.path.join(CONTAINERS_DIR, staged)}' was not fully removed. "
        f"The next install or restore removes it, and so does "
        f"'{PROGRAM_NAME} clear-cache'."
    )


def _assert_swept_once_its_process_is_gone(monkeypatch):
    """The staging directory left goes with the next sweep, maker gone.

    The maker is this very process, so it is made to look like another
    one that took over its process ID: a start time of its own.
    """
    real = build_scratch._process_start
    monkeypatch.setattr(build_scratch, "_process_start",
                        lambda pid: (real(pid) or 0) + 1)
    assert reclaim_staging() == 1
    assert _staged() == []


def _listed():
    return [info.name for info in ProotDistro().list_containers()]


def _raise_on(prefix):
    def handler(event):
        if event.message.startswith(prefix):
            raise _HandlerFailed(event.message)
    return handler


def test_api_install_failure_with_a_raising_handler_leaves_no_container(
    tmp_path,
):
    """Regression: the stop after the handler skipped the removal.

    The handler raises on "Failed to install", so the API stops the
    worker while it reports the failure: the whole extracted tree used
    to stay behind, listed as an installed container.
    """
    archive = _rootfs_archive(tmp_path / "rootfs.tar.gz")

    with pytest.raises(_HandlerFailed):
        ProotDistro(event_handler=_raise_on("Failed to install")).install(
            archive, name="half-installed"
        )

    assert container_entry_lstat("half-installed") is None
    assert _listed() == []


def test_api_restore_failure_with_a_raising_handler_leaves_no_container(
    tmp_path,
):
    """Regression: the stop after the handler cut the removal short.

    API.md promises that a restore which created its container removes
    it again on failure, so no incomplete container is left behind.
    """
    archive = _backup_archive(tmp_path / "backup.tar.gz", "half-restored")

    with pytest.raises(_HandlerFailed):
        ProotDistro(
            event_handler=_raise_on("Failed to restore")
        ).restore(archive)

    assert container_entry_lstat("half-restored") is None
    assert _listed() == []


def test_an_interrupt_while_install_reports_its_failure_removes_it(
    tmp_path,
):
    """Regression: install removed the container after the report."""
    archive = _rootfs_archive(tmp_path / "rootfs.tar.gz")
    context, messages = _context(interrupt_on="Failed to install")

    with pytest.raises(KeyboardInterrupt):
        install_container(str(archive), name="reported", context=context)

    assert container_entry_lstat("reported") is None
    assert not any(m.startswith("What the unfinished") for m in messages)
    assert _staged() == []


def test_an_interrupt_in_the_install_removal_lets_it_finish(
    tmp_path, monkeypatch,
):
    """Regression: an interrupt landing in the removal cut it short."""
    archive = _rootfs_archive(tmp_path / "rootfs.tar.gz")
    calls = _interrupt_removal(monkeypatch, 1)
    context, messages = _context()

    with pytest.raises(KeyboardInterrupt):
        install_container(str(archive), name="late", context=context)

    assert len(calls) == 2
    assert container_entry_lstat("late") is None
    assert not any(m.startswith("What the unfinished") for m in messages)
    assert _staged() == []


def test_a_second_interrupt_stops_the_install_removal_and_says_so(
    tmp_path, monkeypatch,
):
    """Ctrl-C during the extraction, then again during the removal.

    The second one stops the removal, as it stops a build's. What it
    leaves is the staging directory, not a container: nothing is at the
    name or listed, and the user is told it will be swept.
    """
    archive = _rootfs_archive(tmp_path / "rootfs.tar.gz", truncated=False)

    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(install_service, "setup_fake_sysdata", interrupted)
    calls = _interrupt_removal(monkeypatch, 1)
    context, messages = _context()

    with pytest.raises(KeyboardInterrupt):
        install_container(str(archive), name="stopped", context=context)

    assert len(calls) == 1
    assert container_entry_lstat("stopped") is None
    assert _listed() == []
    [staged] = _staged()
    assert messages[-2:] == ["Aborted by user.", _left_message(staged)]

    _assert_swept_once_its_process_is_gone(monkeypatch)


def test_install_names_a_container_it_could_not_remove(
    tmp_path, monkeypatch,
):
    """A removal that fails without any interrupt is reported too."""
    archive = _rootfs_archive(tmp_path / "rootfs.tar.gz")
    _interrupt_removal(monkeypatch, 0, removes=False)
    context, messages = _context()

    with pytest.raises(Exception) as raised:
        install_container(str(archive), name="stuck", context=context)

    assert not isinstance(raised.value, KeyboardInterrupt)
    assert container_entry_lstat("stuck") is None
    [staged] = _staged()
    assert messages[-1] == _left_message(staged)


def test_an_interrupt_in_the_restore_removal_lets_it_finish(
    tmp_path, monkeypatch,
):
    """Regression: an interrupt landing in the removal cut it short."""
    archive = _backup_archive(tmp_path / "backup.tar.gz", "late-restore")
    calls = _interrupt_removal(monkeypatch, 1)
    context, messages = _context()

    with pytest.raises(KeyboardInterrupt):
        restore_container(archive_path=str(archive), context=context)

    assert len(calls) == 2
    assert container_entry_lstat("late-restore") is None
    assert not any(m.startswith("What the unfinished") for m in messages)
    assert _staged() == []


def test_a_second_interrupt_stops_the_restore_removal_and_frees_the_lock(
    tmp_path, monkeypatch,
):
    """Ctrl-C during the extraction, then again during the removal.

    What is left is named to the user, and the container's lock is
    released even though the removal was stopped.
    """
    archive = _backup_archive(
        tmp_path / "backup.tar.gz", "stopped-restore", truncated=False
    )
    real_write = restore_service._write_regular
    writes = []

    def write_then_interrupt(*args, **kwargs):
        writes.append(None)
        if len(writes) == 3:
            raise KeyboardInterrupt
        return real_write(*args, **kwargs)

    monkeypatch.setattr(restore_service, "_write_regular",
                        write_then_interrupt)
    calls = _interrupt_removal(monkeypatch, 1)
    context, messages = _context()

    # Held, as a caller that reports the exception holds it: its
    # traceback keeps restore's frame and the lock in it alive, so the
    # lock is free afterwards only if restore released it.
    with pytest.raises(KeyboardInterrupt) as raised:
        restore_container(archive_path=str(archive), context=context)

    assert len(calls) == 1
    assert container_entry_lstat("stopped-restore") is None
    [staged] = _staged()
    assert messages[-2:] == ["Aborted by user.", _left_message(staged)]
    # A lock of its own: the container lock is re-entrant within a
    # process, but a second open file is refused while one holds it.
    fd = os.open(container_lock_path("stopped-restore"), os.O_RDONLY)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        os.close(fd)
    assert raised.traceback

    _assert_swept_once_its_process_is_gone(monkeypatch)
