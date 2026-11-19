# A build's scratch root -- the rootfs of every stage it built -- must not
# outlive it.
#
# The build removes its root when it ends, which a build process killed
# outright never gets to do, and which an interrupt arriving late used to
# cut short: removing a large root takes a while and comes after the work,
# so that is where a late cancel or Ctrl-C lands. Nothing removed such a
# root afterwards, and they piled up in RUNTIME_DIR/build-tmp.

import os
import re
import signal

import pytest

from proot_distro import statedir
from proot_distro.api import ProotDistro, ProotDistroError, RuntimeConfig
from proot_distro.constants import RUNTIME_DIR
from proot_distro.operations import build as build_operation
from proot_distro.operations.build import build_image

BUILD_TMP = os.path.join(RUNTIME_DIR, "build-tmp")


def _context(parent, dockerfile="FROM scratch\nCOPY rootfs/ /\n"):
    context = parent / "context"
    (context / "rootfs" / "etc").mkdir(parents=True)
    (context / "rootfs" / "etc" / "hostname").write_text("scratch\n")
    (context / "Dockerfile").write_text(dockerfile)
    return context


def _left():
    return sorted(os.listdir(BUILD_TMP)) if os.path.isdir(BUILD_TMP) else []


def _interrupt_scratch_removal(monkeypatch, times):
    """Raise KeyboardInterrupt from the first *times* root removals."""
    real = statedir.remove_state_tree
    calls = []

    def remove(path, **kwargs):
        calls.append(path)
        if len(calls) <= times:
            raise KeyboardInterrupt
        return real(path, **kwargs)

    monkeypatch.setattr(statedir, "remove_state_tree", remove)
    return calls


def test_the_next_build_removes_what_a_killed_build_left(tmp_path):
    """Regression: a build process killed outright left its root forever."""
    context = _context(
        tmp_path, "FROM scratch\nCOPY rootfs/ /\nRUN wait-for-the-kill\n"
    )
    fake_proot = tmp_path / "fake-build-proot"
    # proot's parent is the worker: the stand-in names it, then waits.
    fake_proot.write_text(
        "#!/bin/sh\nprintf 'worker=%s\\n' \"$PPID\"\nexec sleep 30\n"
    )
    fake_proot.chmod(0o755)
    killed = []

    def kill_the_worker(event):
        found = re.search(r"worker=(\d+)", event.message)
        if event.phase == "build-output" and found and not killed:
            killed.append(int(found.group(1)))
            os.kill(killed[0], signal.SIGKILL)

    default = RuntimeConfig.default()
    with pytest.raises(ProotDistroError):
        ProotDistro(RuntimeConfig(
            default.runtime_dir, default.cache_dir, fake_proot
        ), event_handler=kill_the_worker).build(context, tags=["killed:1"])

    assert killed
    left = _left()
    assert len(left) == 1 and left[0].startswith(f"pd-build-{killed[0]}.")

    events = []
    ProotDistro(event_handler=events.append).build(
        _context(tmp_path / "again"), tags=["after:1"]
    )

    assert _left() == []
    assert any(
        event.phase == "build"
        and event.message == "Removing scratch left by 1 interrupted "
        "build(s)..."
        for event in events
    )


def test_an_interrupt_in_the_cleanup_lets_it_finish(tmp_path, monkeypatch):
    """Regression: a late cancel or Ctrl-C stranded the scratch root.

    The work is done when the interrupt lands in the removal; the removal
    runs to the end and the interrupt is raised after it.
    """
    calls = _interrupt_scratch_removal(monkeypatch, 1)
    output = tmp_path / "out.oci.tar"

    with pytest.raises(KeyboardInterrupt):
        build_image(
            str(_context(tmp_path)), tags=["late:1"],
            outputs=(str(output),), quiet=True,
        )

    assert len(calls) == 2
    assert _left() == []
    assert output.is_file()


def test_a_second_interrupt_stops_the_cleanup(tmp_path, monkeypatch):
    # The build is already unwinding from one interrupt, so one arriving
    # in the cleanup is the user insisting: it is not run again, and the
    # root is left for the next build to take. Its descriptor still goes.
    def interrupted(*_args, **_kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(build_operation, "write_oci_archive", interrupted)
    calls = _interrupt_scratch_removal(monkeypatch, 1)
    context = str(_context(tmp_path))
    output = str(tmp_path / "out.oci.tar")
    before = set(os.listdir("/proc/self/fd"))

    with pytest.raises(KeyboardInterrupt):
        build_image(context, tags=["insisted:1"], outputs=(output,),
                    quiet=True)

    assert set(os.listdir("/proc/self/fd")) == before
    assert len(calls) == 1
    assert len(_left()) == 1


def test_an_interrupt_during_the_retry_still_stops_it(
        tmp_path, monkeypatch):
    calls = _interrupt_scratch_removal(monkeypatch, 2)

    with pytest.raises(KeyboardInterrupt):
        build_image(str(_context(tmp_path)), tags=["twice:1"], quiet=True)

    assert len(calls) == 2
    assert len(_left()) == 1
