# Tests for the RUN step's idea of when a step is over — proot's event
# loop ends when its last *tracee* does, not when the step's command
# does, so anything a step leaves running used to keep the build waiting
# on it (off Termux, where proot has no --kill-on-exit) and then went on
# writing into the stage rootfs the layer is diffed from.

import os
import subprocess
import sys
import time

import pytest

from proot_distro.helpers.build_engine import run_step


# A stand-in for proot: a process that outlives the command it started,
# with nothing of its own left to report. `exec` is what leaves it
# childless, which is the state _wait_for_step reads as "the step's
# command is done".
_LINGER = 'sh -c "{inner}"; exec sleep 30'


def _spawn(inner):
    return subprocess.Popen(
        ["sh", "-c", _LINGER.format(inner=inner)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )


def _stop(proc):
    try:
        os.killpg(proc.pid, 9)
    except OSError:
        pass
    proc.wait()


@pytest.fixture
def subreaper():
    """The flag that puts a step's orphans back within reach."""
    if not run_step._become_subreaper():
        pytest.skip("kernel refuses PR_SET_CHILD_SUBREAPER")
    return True


def test_a_step_that_ends_cleanly_is_simply_waited_for(subreaper):
    baseline = set(run_step._adopted())
    proc = subprocess.Popen(["sh", "-c", "exit 5"], start_new_session=True)
    run_step._wait_for_step(proc, baseline)

    assert proc.poll() == 5
    assert run_step._stop_step(proc.pid, baseline, skip_pid=proc.pid) == 0


def test_a_backgrounded_leftover_ends_the_wait_and_is_stopped(subreaper,
                                                              capsys):
    baseline = set(run_step._adopted())
    proc = _spawn("sleep 300 & exit 0")
    try:
        started = time.monotonic()
        run_step._wait_for_step(proc, baseline)
        # The stand-in is still running, exactly as proot would be while
        # it waits on a tracee that outlived the step's command.
        assert proc.poll() is None
        assert time.monotonic() - started < 10

        assert run_step._stop_step(proc.pid, baseline,
                                   skip_pid=proc.pid) >= 1
        assert run_step._leftovers(proc.pid, baseline, proc.pid) == []
        # proot itself is left alone: it is still owed the chance to
        # report the step's exit status.
        assert proc.poll() is None
    finally:
        _stop(proc)
    assert "left 1 process" in capsys.readouterr().err


def test_a_daemonised_leftover_is_found_through_adoption(subreaper):
    # fork, setsid, fork -- the sequence every daemon uses, which leaves
    # the step's process group as well as its process tree. Only the
    # reparenting the subreaper flag buys names it at all.
    baseline = set(run_step._adopted())
    subprocess.run(
        [sys.executable, "-c",
         "import subprocess, sys;"
         "subprocess.Popen([sys.executable, '-c',"
         "'import os, time; os.setsid(); time.sleep(300)'])"],
        check=True,
    )
    deadline = time.monotonic() + 5
    while not run_step._adopted(baseline) and time.monotonic() < deadline:
        time.sleep(0.05)
    adopted = run_step._adopted(baseline)
    assert adopted, "the daemonised process was not reparented here"
    pid = adopted[0]

    # Its own group, not the step's -- there is no step group here at
    # all. Passing this process's own pgid is what a caller must never
    # be able to turn into a signal: _leftovers refuses it and answers
    # from adoption alone, which is the half this test is about.
    assert run_step._stop_step(os.getpgrp(), baseline) >= 1
    deadline = time.monotonic() + 5
    while _alive(pid) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(pid)


def _alive(pid):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except OSError:
        return True
    # A reaped zombie is gone; an unreaped one still answers.
    try:
        with open(f"/proc/{pid}/stat") as fh:
            fields = fh.read()
        return fields[fields.rindex(")") + 1:].split()[0] != "Z"
    except (OSError, ValueError, IndexError):
        return False


def test_the_wait_does_not_cut_a_step_short_over_a_mid_step_orphan(subreaper):
    # A command that orphans something halfway through -- a subshell
    # that finishes, a build tool that forks -- is still running, and
    # the step is not over until it is.
    baseline = set(run_step._adopted())
    proc = subprocess.Popen(
        ["sh", "-c", 'sh -c "sleep 5 & exit 0"; sleep 1'],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        started = time.monotonic()
        run_step._wait_for_step(proc, baseline)
        assert proc.poll() == 0
        assert time.monotonic() - started >= 0.9
    finally:
        _stop(proc)
        run_step._stop_step(proc.pid, baseline)


def test_leftovers_never_names_this_process_or_its_group():
    # A pgid this process shares is a caller error, and the answer to
    # one must not be a SIGTERM to everything sharing the terminal.
    assert os.getpid() not in run_step._leftovers(os.getpgrp(), (), None)
    assert run_step._leftovers(os.getpgrp(), set(run_step._adopted()),
                               None) == []


def test_group_members_skips_what_it_is_told_to(subreaper):
    proc = subprocess.Popen(["sh", "-c", "sleep 5"], start_new_session=True)
    try:
        members = run_step._group_members(proc.pid)
        assert proc.pid in members
        assert proc.pid not in run_step._group_members(
            proc.pid, skip={proc.pid}
        )
    finally:
        _stop(proc)


def test_here_doc_input_is_staged_as_a_file(tmp_path):
    # A file rather than a pipe: nothing is left to feed a pipe once the
    # step is watched instead of waited on, and a body larger than the
    # pipe buffer would deadlock against a step that is not reading yet.
    from types import SimpleNamespace

    engine = SimpleNamespace(tmp_root=str(tmp_path))
    body = "echo one\n" + "# padding\n" * 20000
    fh = run_step._stdin_file(engine, body)
    assert fh is not None
    try:
        out = subprocess.run(["cat"], stdin=fh, capture_output=True)
    finally:
        fh.close()
    assert out.stdout.decode() == body
    assert run_step._stdin_file(engine, None) is None
