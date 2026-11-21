# A Ctrl-C must not vanish inside tarfile.
#
# tf.extractfile() builds an io.BufferedReader over tarfile's raw member
# stream, and BufferedReader's constructor calls that stream's tell() and
# clears whatever it raises. tell() is Python code, so a SIGINT whose
# handler ran there raised KeyboardInterrupt into C that discarded it:
# the extraction went on as if nothing had been pressed. An API cancel is
# a SIGINT to the worker, so about one cancel in a hundred landing
# mid-extraction came back as a finished install or reset (seen on CI as
# "DID NOT RAISE OperationCancelledError" on Python 3.9 and 3.15).
#
# The fixture below presses Ctrl-C at exactly that moment, every time,
# so each place that opens a tar member is held to delivering it.
#
# It does so with a second thread running, because a signal mask is a
# thread's own. Holding SIGINT back by blocking it in the thread that
# opens the member worked only while no other thread existed: the kernel
# hands a signal sent to the process, as a Ctrl-C and an API cancel are,
# to any thread that does not block it, and Python then runs the handler
# in the main thread wherever it next checks -- inside tell() again,
# when that runs slowly. The test suite's own process has another thread
# whenever pytest-rerunfailures is installed, and lost one Ctrl-C there.

import ast
import io
import os
import pathlib
import select
import signal
import tarfile
import threading
import time
from types import SimpleNamespace

import pytest

import proot_distro
from proot_distro import interrupts
from proot_distro.helpers import tar_extract
from proot_distro.helpers.build_engine import copy_step
from proot_distro.operations import install_local
from proot_distro.operations.restore import restore_container


@pytest.fixture
def press_ctrl_c():
    """A function that sends this process SIGINT and returns once it is taken.

    A second thread runs throughout, so the kernel can hand the signal
    to a thread other than the one that sent it. "Taken" means some
    thread's C-level handler has run, which writes the signal number to
    the wakeup fd. Python runs the handler proper in the main thread at
    its next check, which is then always after this returns, in the code
    that called it: the worst place for it, every time.
    """
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    read_end, write_end = os.pipe()
    os.set_blocking(write_end, False)
    previous_wakeup = signal.set_wakeup_fd(write_end,
                                           warn_on_full_buffer=False)
    release = threading.Event()
    bystander = threading.Thread(target=release.wait, daemon=True)
    bystander.start()

    def press():
        os.kill(os.getpid(), signal.SIGINT)
        taken, _, _ = select.select([read_end], [], [], 10)
        assert taken, "no thread took the SIGINT"
        os.read(read_end, 64)

    try:
        yield press
    finally:
        release.set()
        bystander.join()
        signal.set_wakeup_fd(previous_wakeup)
        os.close(read_end)
        os.close(write_end)
        signal.signal(signal.SIGINT, previous)


@pytest.fixture
def ctrl_c_in_member_open(monkeypatch, press_ctrl_c):
    """Press Ctrl-C from inside the first raw tell() call."""
    original = tarfile._FileInFile.tell
    fired = []

    def tell(self):
        if not fired:
            fired.append(True)
            press_ctrl_c()
        return original(self)

    monkeypatch.setattr(tarfile._FileInFile, "tell", tell)
    yield fired


def _tar(members):
    data = io.BytesIO()
    with tarfile.open(fileobj=data, mode="w") as tf:
        for name, payload in members:
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mode = 0o644
            tf.addfile(info, io.BytesIO(payload))
    data.seek(0)
    return data


def test_the_helper_raises_the_ctrl_c_instead_of_losing_it(
        ctrl_c_in_member_open):
    with tarfile.open(fileobj=_tar([("a", b"A")])) as tf:
        member = tf.getmember("a")
        with pytest.raises(KeyboardInterrupt):
            interrupts.open_tar_member(tf, member)
    assert ctrl_c_in_member_open


def test_rootfs_and_layer_extraction_stop(tmp_path, ctrl_c_in_member_open):
    # install's plain-tar path and every OCI layer (install, reset, pull)
    # go through this one extractor.
    archive = tmp_path / "rootfs.tar"
    archive.write_bytes(_tar([(f"f{i}", b"x" * 64) for i in range(5)])
                        .getvalue())
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    fd = os.open(str(rootfs), os.O_RDONLY | os.O_DIRECTORY)
    try:
        with pytest.raises(KeyboardInterrupt):
            tar_extract.extract_tar_to_rootfs(str(archive), fd)
    finally:
        os.close(fd)
    assert ctrl_c_in_member_open
    assert not (rootfs / "f4").exists()


def test_restore_stops(tmp_path, ctrl_c_in_member_open):
    archive = tmp_path / "backup.tar"
    archive.write_bytes(_tar(
        [(f"restored/rootfs/f{i}", b"y" * 64) for i in range(5)]
    ).getvalue())
    with pytest.raises(KeyboardInterrupt):
        restore_container(archive_path=str(archive))
    assert ctrl_c_in_member_open


def test_oci_archive_reads_stop(ctrl_c_in_member_open):
    with tarfile.open(fileobj=_tar([("index.json", b"{}")])) as tf:
        members = {member.name: member for member in tf.getmembers()}
        with pytest.raises(KeyboardInterrupt):
            install_local._oci_read_capped(tf, members, "index.json")
    assert ctrl_c_in_member_open


def test_add_auto_extraction_stops(tmp_path, ctrl_c_in_member_open):
    spool = copy_step._Spool(
        SimpleNamespace(tmp_root=str(tmp_path), tmp_root_fd=None)
    )
    try:
        with pytest.raises(KeyboardInterrupt):
            copy_step._extract_tar_into_dest(
                _tar([("a", b"A" * 64)]), "extracted", {}, 0, 0, spool,
                "payload.tar",
            )
    finally:
        spool.close()
    assert ctrl_c_in_member_open


def test_defer_sigint_raises_after_the_body_has_run(press_ctrl_c):
    reached = []
    with pytest.raises(KeyboardInterrupt):
        with interrupts.defer_sigint():
            press_ctrl_c()
            reached.append(True)
    assert reached
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    assert signal.SIGINT not in signal.pthread_sigmask(signal.SIG_BLOCK, [])


def test_an_uninterrupted_body_raises_nothing():
    previous = signal.signal(signal.SIGINT, signal.default_int_handler)
    try:
        with interrupts.defer_sigint():
            pass
        assert signal.getsignal(signal.SIGINT) is signal.default_int_handler
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt with no Ctrl-C pressed")
    finally:
        signal.signal(signal.SIGINT, previous)


def test_a_ctrl_c_in_the_body_wins_over_its_failure(press_ctrl_c):
    # The caller's cleanup is for BaseException, and the user asked to
    # stop: a body that also failed must not swallow the Ctrl-C.
    with pytest.raises(KeyboardInterrupt) as raised:
        with interrupts.defer_sigint():
            press_ctrl_c()
            raise OSError("the body failed as well")
    assert isinstance(raised.value.__context__, OSError)
    assert signal.getsignal(signal.SIGINT) is signal.default_int_handler


def test_a_handler_of_the_programs_own_is_left_in_place():
    # Its effects are not defer_sigint's to replay, so it stays
    # installed and runs once, as it would have.
    calls = []

    def handler(signum, _frame):
        calls.append(signum)

    previous = signal.signal(signal.SIGINT, handler)
    try:
        with interrupts.defer_sigint():
            installed = signal.getsignal(signal.SIGINT)
            os.kill(os.getpid(), signal.SIGINT)
        deadline = time.monotonic() + 10
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt in place of the program's handler")
    finally:
        signal.signal(signal.SIGINT, previous)
    assert installed is handler
    assert calls == [signal.SIGINT]


def test_defer_sigint_works_outside_the_main_thread():
    # No handler may be set there, and none runs there either.
    outcome = []

    def body():
        try:
            with interrupts.defer_sigint():
                outcome.append(signal.getsignal(signal.SIGINT))
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=body)
    thread.start()
    thread.join()
    assert outcome == [signal.getsignal(signal.SIGINT)]


def test_nothing_opens_a_tar_member_around_the_helper():
    package = pathlib.Path(proot_distro.__file__).parent
    offenders = []
    for path in package.rglob("*.py"):
        if path.name == "interrupts.py" and path.parent == package:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "extractfile"):
                offenders.append(f"{path.relative_to(package)}:{node.lineno}")
    assert offenders == []


def _cleanup_interrupted(times):
    """A cleanup whose first *times* runs are cut short by Ctrl-C."""
    runs = []

    def cleanup():
        runs.append(None)
        if len(runs) <= times:
            raise KeyboardInterrupt

    return cleanup, runs


def test_finish_cleanup_runs_once_when_nothing_interrupts_it():
    cleanup, runs = _cleanup_interrupted(0)

    interrupts.finish_cleanup(cleanup, interrupted=False)

    assert len(runs) == 1


def test_a_first_interrupt_in_a_cleanup_waits_for_it_to_finish():
    cleanup, runs = _cleanup_interrupted(1)

    with pytest.raises(KeyboardInterrupt):
        interrupts.finish_cleanup(cleanup, interrupted=False)

    assert len(runs) == 2


def test_a_second_interrupt_stops_a_cleanup():
    cleanup, runs = _cleanup_interrupted(2)

    with pytest.raises(KeyboardInterrupt):
        interrupts.finish_cleanup(cleanup, interrupted=False)

    assert len(runs) == 2


def test_after_an_interrupt_the_next_one_stops_the_cleanup():
    # The caller is unwinding from the first, so this is the second.
    cleanup, runs = _cleanup_interrupted(1)

    with pytest.raises(KeyboardInterrupt):
        interrupts.finish_cleanup(cleanup, interrupted=True)

    assert len(runs) == 1
