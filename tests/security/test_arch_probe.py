# Containment tests for the ELF probe that decides a container's
# architecture — and therefore whether `login` selects an emulator at all.
#
# The probe used to compose `root + rel` and hand the result to open().
# That resolved the middle of the path on the *host*, so an absolute
# symlink the rootfs ships — or a `usr -> /` a guest leaves behind — had
# it read the host's binaries; and open() on a FIFO blocks until a peer
# appears, which a guest that plants one at /bin/sh never provides. The
# candidates are read through the guest walk now, the same one login
# takes the container's passwd out of.

import contextlib
import os
import signal

import pytest

from _builders import write_elf
from proot_distro import arch
from proot_distro.arch import detect_installed_arch, get_device_cpu_arch
from proot_distro.paths import (
    container_dir, container_rootfs, open_container_pair,
)


# The candidates, in the order the probe tries them.
FIRST_CANDIDATES = ("usr/bin/bash", "usr/bin/sh", "usr/bin/su",
                    "usr/bin/busybox")

# Something that is definitely not this host, so "read the host's
# binaries" and "read the container's" give different answers.
GUEST_ARCH = "riscv64" if get_device_cpu_arch() != "riscv64" else "aarch64"


@contextlib.contextmanager
def _deadline(seconds=20):
    """Fail rather than hang.

    The regression this guards is a *blocking* call, so an assertion
    after the fact never runs. SIGALRM interrupts the open and the
    exception raised from the handler propagates out of it (PEP 475
    retries on EINTR only when the handler returns normally). Done here
    rather than with pytest-timeout so the guard does not depend on a
    plugin being installed.
    """
    def _fire(_sig, _frame):
        raise AssertionError(
            f"the probe did not return within {seconds}s — a blocking "
            f"open() on a planted FIFO is the regression this test is for"
        )

    previous = signal.signal(signal.SIGALRM, _fire)
    signal.setitimer(signal.ITIMER_REAL, seconds)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


def _clear(root, rels=FIRST_CANDIDATES):
    """Remove the earlier candidates so a later one decides the answer."""
    for rel in rels:
        path = os.path.join(root, rel)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        if os.path.lexists(path):
            os.remove(path)


@pytest.fixture
def box(builders):
    builders.make_container("box", arch=GUEST_ARCH)
    return container_rootfs("box")


# --- a FIFO must not block the probe ---------------------------------------

def test_a_planted_fifo_does_not_hang_the_probe(box):
    """open() on a FIFO waits for a writer a hostile guest never supplies.

    The whole login hung for as long as the user left it running.
    open_regular_at()'s O_NONBLOCK plus its fstat is what steps over it.
    """
    _clear(box)
    for rel in FIRST_CANDIDATES:
        os.mkfifo(os.path.join(box, rel))

    with _deadline():
        # The FIFOs are stepped over and a real candidate still answers.
        assert detect_installed_arch(box) == GUEST_ARCH


def test_a_rootfs_of_nothing_but_fifos_is_unknown(builders, tmp_path):
    root = tmp_path / "rootfs"
    (root / "bin").mkdir(parents=True)
    os.mkfifo(str(root / "bin" / "sh"))
    with _deadline():
        assert detect_installed_arch(str(root)) == "unknown"


# --- the probe must stay inside the rootfs ---------------------------------

def test_an_absolute_symlink_resolves_inside_the_rootfs(box):
    """`/usr/bin/bash -> /bin/bash` is an ordinary thing for a rootfs to ship.

    Composed onto the host, that absolute target left the rootfs: the
    probe read the *host's* bash and reported the host's architecture,
    so an emulated container got no emulator and could not start. The
    walk re-anchors it at the guest's "/" the way proot does.
    """
    _clear(box)
    os.symlink("/bin/bash", os.path.join(box, "usr", "bin", "bash"))
    write_elf(os.path.join(box, "bin", "bash"), GUEST_ARCH)

    assert detect_installed_arch(box) == GUEST_ARCH
    assert GUEST_ARCH != get_device_cpu_arch()


def test_a_planted_component_cannot_reach_host_binaries(builders, tmp_path):
    """A guest-shipped `usr -> /` used to send the probe to the host."""
    root = tmp_path / "rootfs"
    root.mkdir()
    os.symlink("/", str(root / "usr"))
    # Nothing inside the rootfs is an ELF, so anything but "unknown"
    # means the probe read something it should not have.
    assert detect_installed_arch(str(root)) == "unknown"


# --- the probe answers for the rootfs that was pinned ----------------------

def test_the_probe_reads_the_pinned_rootfs(builders, tmp_path):
    """A swap after the pin does not change which container is measured."""
    builders.make_container("box", arch=GUEST_ARCH)
    decoy = builders.make_container("decoy", arch=get_device_cpu_arch())

    container_fd, rootfs_fd = open_container_pair("box")
    try:
        target = container_dir("box")
        os.rename(target, target + ".moved")
        os.symlink(decoy, target)

        assert detect_installed_arch(
            container_rootfs("box"), rootfs_fd=rootfs_fd) == GUEST_ARCH
        # ...and by name, which is what passing the descriptor replaces.
        assert detect_installed_arch(
            container_rootfs("box")) == get_device_cpu_arch()
    finally:
        os.close(rootfs_fd)
        os.close(container_fd)


# --- the header itself -----------------------------------------------------

def test_only_the_header_is_ever_read(box, monkeypatch):
    """How much is read is this program's ceiling, never the file's."""
    seen = []
    real = arch.read_guest_bytes

    def spy(root, path, size, **kw):
        seen.append(size)
        return real(root, path, size, **kw)

    monkeypatch.setattr(arch, "read_guest_bytes", spy)
    detect_installed_arch(box)
    assert seen and set(seen) == {arch._ELF_HEADER_BYTES}
