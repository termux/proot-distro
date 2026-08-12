#
# A hardlink is the one thing O_NOFOLLOW cannot see: it is not a link to a
# file, it *is* the file, under a second name. Nothing distinguishes one a
# guest made to a host file from an ordinary rootfs entry — same uid, same
# filesystem, no race to win — so `copy` and `sync` hold to a single rule:
# a transfer never writes to an inode it did not create itself.
#
# dirfd keeps that rule for content (open_new_at is O_EXCL, and a
# destination that may exist is written to a temp file and renamed over).
# sync's up-to-date path used to break it for *metadata*: a file whose
# size and mtime already matched was never rewritten, so the mode was
# fixed in place with fchmod on whatever inode the destination name held.
# On Termux that inode can be a host file — $TERMUX_PREFIX is bound into
# every non-isolated container by default, and RUNTIME_DIR lives under it.

import os
import stat

from types import SimpleNamespace

from proot_distro.commands.sync import command_sync
from proot_distro.paths import container_rootfs


def _sync(source, destination, **over):
    base = dict(source=source, destination=destination, verbose=False,
                checksum=False, delete=False)
    base.update(over)
    command_sync(SimpleNamespace(**base))


def _plant(rootfs, victim, mode, content=None, times=None):
    """Set up a guest that has hardlinked *victim* into its own rootfs.

    The source entry is given the victim's size and, by default, its mtime,
    so the change detector finds nothing to rewrite — which is what hands
    the entry to the metadata path. Pass *content* to make the two compare
    equal under --checksum as well, and *times* to leave only the
    timestamps out of step.
    """
    st = os.stat(victim)
    src = os.path.join(rootfs, "src")
    dst = os.path.join(rootfs, "dst")
    os.makedirs(src)
    os.makedirs(dst)
    with open(os.path.join(src, "x"), "w") as fh:
        fh.write(content if content is not None else "S" * st.st_size)
    os.chmod(os.path.join(src, "x"), mode)
    if times is None:
        os.utime(os.path.join(src, "x"), ns=(st.st_atime_ns, st.st_mtime_ns))
    else:
        os.utime(os.path.join(src, "x"), times)
    os.link(victim, os.path.join(dst, "x"))


def test_sync_does_not_chmod_through_a_hardlink(tmp_path, builders):
    """The guest's chosen mode must not reach the host's inode."""
    builders.make_container("box")
    rootfs = container_rootfs("box")
    victim = tmp_path / "secret.key"
    victim.write_text("PRIVATE-KEY-DATA\n")
    os.chmod(victim, 0o600)

    _plant(rootfs, str(victim), 0o777)
    _sync("box:/src", "box:/dst")

    assert stat.S_IMODE(os.stat(victim).st_mode) == 0o600
    assert victim.read_text() == "PRIVATE-KEY-DATA\n"


def test_sync_does_not_utime_through_a_hardlink(tmp_path, builders):
    """Nor its chosen timestamps, which the same call now also applies.

    Only --checksum reaches this: without it a differing mtime is itself
    what makes the file out of date, and the rewrite path — which builds a
    new inode — handles it. With it the contents decide, so an entry that
    matches byte for byte is left to the metadata call, timestamps and all.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    victim = tmp_path / "stamped"
    victim.write_text("host content\n")
    os.utime(victim, (1_500_000_000, 1_500_000_000))

    # Identical content and mode, so only the times are out of step.
    _plant(rootfs, str(victim), stat.S_IMODE(os.stat(victim).st_mode),
           content="host content\n", times=(1_000_000_000, 1_000_000_000))
    _sync("box:/src", "box:/dst", checksum=True)

    assert int(os.stat(victim).st_mtime) == 1_500_000_000
    assert victim.read_text() == "host content\n"
    # ...and the destination still ends up stamped like the source.
    landed = os.path.join(rootfs, "dst", "x")
    assert int(os.stat(landed).st_mtime) == 1_000_000_000


def test_sync_breaks_the_link_instead_of_leaving_the_mode_wrong(tmp_path,
                                                               builders):
    """Refusing the chmod must not mean giving up on the destination.

    The entry is rewritten instead — temp file, rename — which leaves the
    other name pointing at the untouched old inode and gives the
    destination the mode it was supposed to get. Both halves matter: a
    plain skip would quietly stop syncing modes for any file the user
    themselves had hardlinked.
    """
    builders.make_container("box")
    rootfs = container_rootfs("box")
    victim = tmp_path / "secret.key"
    victim.write_text("PRIVATE-KEY-DATA\n")
    os.chmod(victim, 0o600)

    _plant(rootfs, str(victim), 0o777)
    _sync("box:/src", "box:/dst")

    landed = os.path.join(rootfs, "dst", "x")
    assert stat.S_IMODE(os.stat(landed).st_mode) == 0o777
    assert os.stat(landed).st_nlink == 1
    assert not os.path.samefile(landed, victim)
    assert stat.S_IMODE(os.stat(victim).st_mode) == 0o600
