# A failed restore names its cause: a damaged archive, an archive that
# cannot be read, or a container that cannot be written.
#
# Every OSError used to share the handler for a damaged archive, which
# said "The archive either was corrupted or has unexpected structure."
# and raised IntegrityFailure (IntegrityError in the API). A full disk
# while the container was written, or a read-only containers directory,
# said the backup was bad. Since a restore over a container removes the
# old one first (container_staging.clear_container), that could have the
# user throw away the one good copy left.

import errno
import io
import os
from types import SimpleNamespace

import pytest

import _builders as builders
from proot_distro.api import IntegrityError, OperationError, ProotDistro
from proot_distro.commands.restore import command_restore
from proot_distro.constants import CONTAINERS_DIR, PROGRAM_NAME
from proot_distro.operation_errors import IntegrityFailure, OperationFailure
from proot_distro.operations import restore as restore_service
from proot_distro.operations.backup import backup_container
from proot_distro.operations.containers import remove_container
from proot_distro.operations.context import OperationContext
from proot_distro.operations.restore import restore_container
from proot_distro.paths import container_entry_lstat

CORRUPTED = "The archive either was corrupted or has unexpected structure."
FREE_SPACE = "Free up some space and restore the archive again."
RESTORE_AGAIN = ("Container 'box' was removed, but the restore did not "
                 "finish. Restore the archive again to bring it back.")


@pytest.fixture
def backup(tmp_path):
    builders.make_container("box", files={"root/work": b"backed up"})
    path = tmp_path / "box.tar.gz"
    backup_container("box", output_path=str(path))
    return str(path)


def _context():
    messages = []
    return OperationContext(
        emit=lambda _kind, text, _phase, _res: messages.append(text)
    ), messages


def test_a_full_disk_is_no_damaged_archive(backup, monkeypatch):
    """Regression: ENOSPC while writing said the archive was corrupted."""
    def full(*_args, **_kwargs):
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(restore_service, "_write_regular", full)
    context, messages = _context()

    with pytest.raises(OperationFailure) as exc:
        restore_container(archive_path=backup, context=context)

    assert not isinstance(exc.value, IntegrityFailure)
    assert CORRUPTED not in messages
    assert ("Failed to restore container: cannot write the container: "
            "No space left on device") in messages
    assert FREE_SPACE in messages
    assert FREE_SPACE in str(exc.value)
    # The old container went first, so the user is told how to get it
    # back -- and the archive is not called bad.
    assert container_entry_lstat("box") is None
    assert RESTORE_AGAIN in messages
    # And the exception carries it, for the API's message.
    assert exc.value.recovery == RESTORE_AGAIN


def test_an_archive_that_cannot_be_read_is_no_damaged_archive():
    """An I/O error reading the input is not a corrupted archive."""
    class Failing(io.RawIOBase):
        def __init__(self):
            self.calls = 0

        def readable(self):
            return True

        def read(self, size=-1):
            self.calls += 1
            if self.calls == 1:
                return b"\x1f\x8b\x08\x00\x00\x00"   # a gzip header
            raise OSError(errno.EIO, "Input/output error")

    context, messages = _context()
    with pytest.raises(OperationFailure) as exc:
        restore_container(archive_file=Failing(), context=context)

    assert not isinstance(exc.value, IntegrityFailure)
    assert str(exc.value) == "cannot read the archive: Input/output error"
    assert CORRUPTED not in messages


def test_a_damaged_archive_is_still_called_one(tmp_path):
    junk = tmp_path / "junk.tar.gz"
    junk.write_bytes(b"\x1f\x8b" + b"not gzip at all" * 40)
    context, messages = _context()
    with pytest.raises(IntegrityFailure):
        restore_container(archive_path=str(junk), context=context)
    assert CORRUPTED in messages


@pytest.mark.skipif(os.geteuid() == 0,
                    reason="root writes a read-only directory anyway")
def test_the_cli_and_the_api_say_the_container_could_not_be_written(
        backup, capsys):
    remove_container("box")
    os.chmod(CONTAINERS_DIR, 0o555)
    try:
        with pytest.raises(SystemExit) as exc:
            command_restore(SimpleNamespace(archive=backup))
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "cannot write the container: Permission denied" in err
        assert CORRUPTED not in err
        # Said once: the CLI does not repeat what the restore reported.
        assert err.count("cannot write the container") == 1

        with pytest.raises(OperationError) as api:
            ProotDistro().restore(backup)
        assert not isinstance(api.value, IntegrityError)
        assert "cannot write the container" in str(api.value)
    finally:
        os.chmod(CONTAINERS_DIR, 0o755)
