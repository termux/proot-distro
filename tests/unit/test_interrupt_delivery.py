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
# so each place that opens a tar member is held to delivering it. It
# presses Ctrl-\ too, which the CLI turns into the same
# KeyboardInterrupt and which was lost the same way.
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
import subprocess
import sys
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


# What each key sends. The CLI gives both signals Python's
# default_int_handler, and so does the fixture.
_KEYS = {"ctrl-c": signal.SIGINT, "ctrl-backslash": signal.SIGQUIT}


@pytest.fixture(params=sorted(_KEYS))
def press_key(request):
    """A function pressing Ctrl-C or Ctrl-\\ that returns once it is taken.

    A second thread runs throughout, so the kernel can hand the signal
    to a thread other than the one that sent it. "Taken" means some
    thread's C-level handler has run, which writes the signal number to
    the wakeup fd. Python runs the handler proper in the main thread at
    its next check, which is then always after this returns, in the code
    that called it: the worst place for it, every time. The signal sent
    is the function's `signum`.
    """
    signum = _KEYS[request.param]
    previous = {
        key: signal.signal(key, signal.default_int_handler)
        for key in _KEYS.values()
    }
    read_end, write_end = os.pipe()
    os.set_blocking(write_end, False)
    previous_wakeup = signal.set_wakeup_fd(write_end,
                                           warn_on_full_buffer=False)
    release = threading.Event()
    bystander = threading.Thread(target=release.wait, daemon=True)
    bystander.start()

    def press():
        os.kill(os.getpid(), signum)
        taken, _, _ = select.select([read_end], [], [], 10)
        assert taken, "no thread took the signal"
        os.read(read_end, 64)

    press.signum = signum
    try:
        yield press
    finally:
        release.set()
        bystander.join()
        signal.set_wakeup_fd(previous_wakeup)
        os.close(read_end)
        os.close(write_end)
        for key, handler in previous.items():
            signal.signal(key, handler)


@pytest.fixture
def key_in_member_open(monkeypatch, press_key):
    """Press the key from inside the first raw tell() call."""
    original = tarfile._FileInFile.tell
    fired = []

    def tell(self):
        if not fired:
            fired.append(True)
            press_key()
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
        key_in_member_open):
    with tarfile.open(fileobj=_tar([("a", b"A")])) as tf:
        member = tf.getmember("a")
        with pytest.raises(KeyboardInterrupt):
            interrupts.open_tar_member(tf, member)
    assert key_in_member_open


def test_rootfs_and_layer_extraction_stop(tmp_path, key_in_member_open):
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
    assert key_in_member_open
    assert not (rootfs / "f4").exists()


def test_restore_stops(tmp_path, key_in_member_open):
    archive = tmp_path / "backup.tar"
    archive.write_bytes(_tar(
        [(f"restored/rootfs/f{i}", b"y" * 64) for i in range(5)]
    ).getvalue())
    with pytest.raises(KeyboardInterrupt):
        restore_container(archive_path=str(archive))
    assert key_in_member_open


def test_oci_archive_reads_stop(key_in_member_open):
    with tarfile.open(fileobj=_tar([("index.json", b"{}")])) as tf:
        members = {member.name: member for member in tf.getmembers()}
        with pytest.raises(KeyboardInterrupt):
            install_local._oci_read_capped(tf, members, "index.json")
    assert key_in_member_open


def test_add_auto_extraction_stops(tmp_path, key_in_member_open):
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
    assert key_in_member_open


def _let_handlers_run():
    """Pass the checks at which Python runs a signal handler."""
    for _ in range(1000):
        pass


def _handlers():
    return {key: signal.getsignal(key) for key in _KEYS.values()}


def _blocked():
    return signal.pthread_sigmask(signal.SIG_BLOCK, [])


def test_defer_interrupts_raises_after_the_body_has_run(press_key):
    reached = []
    with pytest.raises(KeyboardInterrupt):
        with interrupts.defer_interrupts():
            press_key()
            _let_handlers_run()
            reached.append(True)
    assert reached
    assert set(_handlers().values()) == {signal.default_int_handler}
    assert not set(_KEYS.values()) & _blocked()


def test_an_uninterrupted_body_raises_nothing(press_key):
    # press_key gives both keys the CLI's handler; nothing is pressed.
    try:
        with interrupts.defer_interrupts():
            _let_handlers_run()
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt with no key pressed")
    assert set(_handlers().values()) == {signal.default_int_handler}


def test_a_key_in_the_body_wins_over_its_failure(press_key):
    # The caller's cleanup is for BaseException, and the user asked to
    # stop: a body that also failed must not swallow the key.
    with pytest.raises(KeyboardInterrupt) as raised:
        with interrupts.defer_interrupts():
            press_key()
            _let_handlers_run()
            raise OSError("the body failed as well")
    assert isinstance(raised.value.__context__, OSError)
    assert set(_handlers().values()) == {signal.default_int_handler}


def test_a_noting_handler_left_behind_acts_as_the_default(press_key):
    # Putting the two default handlers back is two steps, and the key
    # whose handler went back first can cut it short before the other.
    # The noting handler left installed must not then swallow every
    # later press of that key without a word.
    with interrupts.defer_interrupts():
        noting = signal.getsignal(press_key.signum)
    assert noting is not signal.default_int_handler
    signal.signal(press_key.signum, noting)
    try:
        with pytest.raises(KeyboardInterrupt):
            press_key()
            _let_handlers_run()
    finally:
        restored = signal.getsignal(press_key.signum)
        signal.signal(press_key.signum, signal.default_int_handler)
    assert restored is signal.default_int_handler


@pytest.mark.parametrize("key", sorted(_KEYS))
def test_a_handler_of_the_programs_own_is_left_in_place(key):
    # Its effects are not defer_interrupts' to replay, so it stays
    # installed and runs once, as it would have.
    signum = _KEYS[key]
    calls = []

    def handler(number, _frame):
        calls.append(number)

    previous = signal.signal(signum, handler)
    try:
        with interrupts.defer_interrupts():
            installed = signal.getsignal(signum)
            os.kill(os.getpid(), signum)
        deadline = time.monotonic() + 10
        while not calls and time.monotonic() < deadline:
            time.sleep(0.01)
    except KeyboardInterrupt:
        pytest.fail("KeyboardInterrupt in place of the program's handler")
    finally:
        signal.signal(signum, previous)
    assert installed is handler
    assert calls == [signum]


def test_a_signal_with_no_python_handler_is_left_alone():
    # The API worker leaves SIGQUIT at SIG_DFL, which raises nothing,
    # so there is nothing to hold back and the signal is not blocked.
    previous = signal.signal(signal.SIGQUIT, signal.SIG_DFL)
    try:
        with interrupts.defer_interrupts():
            installed = signal.getsignal(signal.SIGQUIT)
            blocked = _blocked()
    finally:
        signal.signal(signal.SIGQUIT, previous)
    assert installed is signal.SIG_DFL
    assert signal.SIGQUIT not in blocked


def test_defer_interrupts_works_outside_the_main_thread():
    # No handler may be set there, and none runs there either.
    outcome = []

    def body():
        try:
            with interrupts.defer_interrupts():
                outcome.append(_handlers())
        except BaseException as exc:
            outcome.append(exc)

    thread = threading.Thread(target=body)
    thread.start()
    thread.join()
    assert outcome == [_handlers()]


def test_the_cli_gives_ctrl_backslash_the_handler_that_is_held_back():
    # defer_interrupts() swaps only Python's own default_int_handler, so
    # a function of the CLI's own for SIGQUIT would be left in place and
    # its KeyboardInterrupt lost inside tell() as before.
    probe = (
        "import signal, sys\n"
        "from proot_distro import cli\n"
        "sys.argv = ['proot-distro', '--help']\n"
        "try:\n"
        "    cli.main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "held = (signal.getsignal(signal.SIGQUIT)\n"
        "        is signal.default_int_handler)\n"
        "sys.stderr.write(f'SIGQUIT held back: {held}\\n')\n"
    )
    root = pathlib.Path(proot_distro.__file__).resolve().parent.parent
    env = dict(os.environ, PYTHONPATH=str(root))
    result = subprocess.run(
        [sys.executable, "-c", probe], env=env, capture_output=True,
        text=True, timeout=120,
    )
    assert "SIGQUIT held back: True" in result.stderr, result.stderr


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
