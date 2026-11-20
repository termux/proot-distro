# A build's scratch root -- the rootfs of every stage it built -- must not
# outlive it.
#
# The build removes its root when it ends, which a build process killed
# outright never gets to do, and which an interrupt arriving late used to
# cut short: removing a large root takes a while and comes after the work,
# so that is where a late cancel or Ctrl-C lands. Nothing removed such a
# root afterwards, and they piled up in RUNTIME_DIR/build-tmp -- or in the
# system temporary directory, where a build makes its root when build-tmp
# cannot be used. The next build and `clear-cache` now sweep both.

import os
import re
import signal
import subprocess
import sys
import tempfile

import pytest

from proot_distro import statedir
from proot_distro.api import ProotDistro, ProotDistroError, RuntimeConfig
from proot_distro.constants import RUNTIME_DIR
from proot_distro.operations import build as build_operation
from proot_distro.operations.build import build_image
from proot_distro.operations.cache import clear_cache
from proot_distro.operations.context import OperationContext

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


def _build_killed_in_a_run_step(tmp_path):
    """Build through the API and SIGKILL the worker in its RUN step.

    Returns the worker's process ID.
    """
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
    return killed[0]


def _swept(events, count=1):
    return any(
        event.phase == "build"
        and event.message == f"Removing scratch left by {count} "
        "interrupted build(s)..."
        for event in events
    )


@pytest.fixture
def system_tmp(tmp_path, monkeypatch):
    """A system temporary directory of this test's own, workers included."""
    path = tmp_path / "system-tmp"
    path.mkdir()
    monkeypatch.setenv("TMPDIR", str(path))
    monkeypatch.setattr(tempfile, "tempdir", str(path))
    return path


def _scratch_in(directory):
    return sorted(
        name for name in os.listdir(directory) if name.startswith("pd-build-")
    )


def test_the_next_build_removes_what_a_killed_build_left(tmp_path):
    """Regression: a build process killed outright left its root forever."""
    worker = _build_killed_in_a_run_step(tmp_path)

    left = _left()
    assert len(left) == 1 and left[0].startswith(f"pd-build-{worker}-")

    events = []
    ProotDistro(event_handler=events.append).build(
        _context(tmp_path / "again"), tags=["after:1"]
    )

    assert _left() == []
    assert _swept(events)


def test_the_next_build_removes_what_a_killed_fallback_build_left(
        tmp_path, system_tmp):
    """Regression: a root in the system temp dir was never removed.

    A build falls back there when build-tmp cannot be used, here because
    something else stands under the name. mkdtemp() named that root
    pd-build- and eight random characters, so no sweep could tell whose
    it was, and none looked there.
    """
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    os.makedirs(RUNTIME_DIR, exist_ok=True)
    os.symlink(str(elsewhere), BUILD_TMP)

    worker = _build_killed_in_a_run_step(tmp_path)

    left = _scratch_in(system_tmp)
    assert len(left) == 1 and left[0].startswith(f"pd-build-{worker}-")
    assert os.listdir(elsewhere) == []

    # The next build makes its own root in build-tmp, and still looks in
    # the temp dir.
    os.unlink(BUILD_TMP)
    events = []
    ProotDistro(event_handler=events.append).build(
        _context(tmp_path / "again"), tags=["after:1"]
    )

    assert _scratch_in(system_tmp) == []
    assert _left() == []
    assert _swept(events)


@pytest.mark.parametrize("flags", [
    {}, {"orphan": True}, {"build_cache": True},
])
def test_clear_cache_removes_what_killed_builds_left(system_tmp, flags):
    """Regression: only the next build ever removed a dead build's root."""
    child = subprocess.Popen([sys.executable, "-c", ""])
    child.wait()
    roots = [
        os.path.join(BUILD_TMP, f"pd-build-{child.pid}.0badc0de"),
        os.path.join(system_tmp, f"pd-build-{child.pid}-4242.1badc0de"),
    ]
    for root in roots:
        os.makedirs(os.path.join(root, "stage-0", "rootfs", "etc"))
    messages = []
    context = OperationContext(
        emit=lambda _kind, message, _phase, _resource: messages.append(
            message
        )
    )

    clear_cache(context=context, **flags)

    assert [root for root in roots if os.path.lexists(root)] == []
    assert messages[-1] == "Removing scratch left by 2 interrupted build(s)..."


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
