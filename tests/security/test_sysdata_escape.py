# Containment tests for the per-container sysdata directory — the fake
# /proc and /sys content proot bind-mounts into the guest.
#
# The directory sits next to the rootfs, which on Termux is under the
# $TERMUX_PREFIX bound read-write into every non-isolated container, so a
# session can replace any entry in it between runs. Naming those entries
# was enough to have setup_fake_sysdata() create or chmod a host file and
# to have the resulting --bind hand that host file to the guest as
# /proc/loadavg.

import os
import stat

import pytest

from proot_distro import sysdata


@pytest.fixture
def env(tmp_path, monkeypatch):
    # The CPU entries follow the host's cpuinfo; pin it so the content a
    # test expects cannot change between the write and the comparison.
    cpuinfo = tmp_path / "cpuinfo"
    cpuinfo.write_text("".join(f"processor\t: {n}\n\n" for n in range(4)))
    monkeypatch.setattr(sysdata, "_CPUINFO", str(cpuinfo))
    container = tmp_path / "container"
    rootfs = container / "rootfs"
    rootfs.mkdir(parents=True)
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    return container, rootfs, outside


def _sysdata(container):
    return container / "sysdata"


def _expected(content):
    # An entry that follows the host's CPUs is a function of their ids.
    if callable(content):
        return content(sysdata.host_cpu_ids())
    return content


# --- setup -----------------------------------------------------------------

def test_setup_creates_the_expected_tree(env):
    container, rootfs, _outside = env
    sysdata.setup_fake_sysdata(str(rootfs))

    sd = _sysdata(container)
    assert sd.is_dir()
    assert stat.S_IMODE(sd.stat().st_mode) == 0o700
    assert (sd / "sys_empty").is_dir()
    for name, _real, content in sysdata._FAKE_ENTRIES:
        assert (sd / name).read_text() == _expected(content)


def test_setup_keeps_existing_content(env):
    container, rootfs, _outside = env
    sd = _sysdata(container)
    sd.mkdir()
    (sd / "loadavg").write_text("custom\n")

    sysdata.setup_fake_sysdata(str(rootfs))
    assert (sd / "loadavg").read_text() == "custom\n"


def test_setup_does_not_write_through_a_dangling_symlink(env):
    container, rootfs, outside = env
    sd = _sysdata(container)
    sd.mkdir()
    victim = outside / "new-host-file"
    os.symlink(str(victim), str(sd / "loadavg"))

    sysdata.setup_fake_sysdata(str(rootfs))

    assert not victim.exists()
    # The planted link was dropped and the real file made in its place.
    assert not (sd / "loadavg").is_symlink()
    assert (sd / "loadavg").read_text() == sysdata._FAKE_LOADAVG


def test_setup_does_not_leave_a_host_file_bound(env):
    container, rootfs, outside = env
    sd = _sysdata(container)
    sd.mkdir()
    victim = outside / "secret"
    victim.write_text("host secret\n")
    os.symlink(str(victim), str(sd / "stat"))

    sysdata.setup_fake_sysdata(str(rootfs))

    assert victim.read_text() == "host secret\n"
    assert (sd / "stat").read_text() == sysdata.fake_stat(
        sysdata.host_cpu_ids())


def test_setup_refuses_a_symlinked_sysdata_dir(env):
    container, rootfs, outside = env
    os.symlink(str(outside), str(_sysdata(container)))

    sysdata.setup_fake_sysdata(str(rootfs))

    # Nothing was written into the host directory the link pointed at,
    # and the link itself was replaced with a real directory.
    assert os.listdir(str(outside)) == []
    assert stat.S_IMODE(outside.stat().st_mode) == 0o700
    assert not _sysdata(container).is_symlink()
    assert (_sysdata(container) / "loadavg").is_file()


def test_setup_refuses_a_symlinked_sys_empty(env):
    container, rootfs, outside = env
    sd = _sysdata(container)
    sd.mkdir()
    os.symlink(str(outside), str(sd / "sys_empty"))

    sysdata.setup_fake_sysdata(str(rootfs))

    assert not (sd / "sys_empty").is_symlink()
    assert (sd / "sys_empty").is_dir()
    assert os.listdir(str(outside)) == []


def test_setup_drops_a_hardlink_to_a_host_file(env):
    # A hardlink is a regular file, so the type test alone kept it and
    # every later session treated the victim's inode as its own fake
    # /proc content -- the persistent case this module rules out.
    container, rootfs, outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    sd = _sysdata(container)
    victim = outside / "secret"
    victim.write_text("host secret\n")

    (sd / "loadavg").unlink()
    os.link(str(victim), str(sd / "loadavg"))

    sysdata.setup_fake_sysdata(str(rootfs))

    assert (sd / "loadavg").read_text() == sysdata._FAKE_LOADAVG
    assert (sd / "loadavg").stat().st_nlink == 1
    # Unlinking the planted name never touches the file it was linked to.
    assert victim.read_text() == "host secret\n"
    assert victim.stat().st_nlink == 1


def test_setup_keeps_a_file_it_wrote_itself(env):
    # The link-count test must not turn "already there" into "rewrite it
    # every time": a single-link file is this module's own.
    container, rootfs, _outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    sd = _sysdata(container)
    before = (sd / "loadavg").stat().st_ino

    sysdata.setup_fake_sysdata(str(rootfs))
    assert (sd / "loadavg").stat().st_ino == before


def test_setup_steps_over_a_directory_in_the_way(env):
    container, rootfs, _outside = env
    sd = _sysdata(container)
    (sd / "loadavg").mkdir(parents=True)

    sysdata.setup_fake_sysdata(str(rootfs))

    # Nothing to unlink a directory with here; the entry is left alone
    # and the rest of the tree is still written.
    assert (sd / "loadavg").is_dir()
    assert (sd / "uptime").read_text() == sysdata.fake_uptime(
        sysdata.host_cpu_ids())


# The CPU entries are rewritten when the host's CPU list changes, so unlike
# the others they are replaced on a normal run. The replacement must be as
# contained as the creation: it renames over the name, never writes through
# whatever the name currently is.

def _stale(sd, name="stat"):
    # Content the next setup will find different, forcing a rewrite.
    (sd / name).write_text("stale\n")


def test_refresh_does_not_write_through_a_hardlink(env):
    container, rootfs, outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    sd = _sysdata(container)
    victim = outside / "secret"
    victim.write_text("host secret\n")
    (sd / "stat").unlink()
    os.link(str(victim), str(sd / "stat"))

    sysdata.setup_fake_sysdata(str(rootfs))

    assert victim.read_text() == "host secret\n"
    assert victim.stat().st_nlink == 1
    assert (sd / "stat").stat().st_nlink == 1
    assert (sd / "stat").read_text() == sysdata.fake_stat(
        sysdata.host_cpu_ids())


def test_refresh_does_not_trust_a_hardlink_with_the_right_content(env):
    # The content comparison must not turn a linked host file into the
    # container's own: a victim that already reads as the fake text is
    # still another name for the host's inode.
    container, rootfs, outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    sd = _sysdata(container)
    victim = outside / "secret"
    victim.write_text(sysdata.fake_stat(sysdata.host_cpu_ids()))
    (sd / "stat").unlink()
    os.link(str(victim), str(sd / "stat"))

    sysdata.setup_fake_sysdata(str(rootfs))

    assert (sd / "stat").stat().st_nlink == 1
    assert (sd / "stat").stat().st_ino != victim.stat().st_ino
    assert victim.stat().st_nlink == 1


def test_refresh_does_not_trust_a_symlink_with_the_right_content(env):
    container, rootfs, outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    sd = _sysdata(container)
    victim = outside / "secret"
    victim.write_text(sysdata.fake_uptime(sysdata.host_cpu_ids()))
    (sd / "uptime").unlink()
    os.symlink(str(victim), str(sd / "uptime"))

    sysdata.setup_fake_sysdata(str(rootfs))

    assert not (sd / "uptime").is_symlink()
    assert victim.read_text() == sysdata.fake_uptime(sysdata.host_cpu_ids())


def test_refresh_replaces_a_symlink_without_following_it(env):
    container, rootfs, outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    sd = _sysdata(container)
    victim = outside / "secret"
    victim.write_text("host secret\n")
    (sd / "stat").unlink()
    os.symlink(str(victim), str(sd / "stat"))
    (sd / "uptime").unlink()
    os.symlink(str(outside / "new-host-file"), str(sd / "uptime"))

    sysdata.setup_fake_sysdata(str(rootfs))

    assert victim.read_text() == "host secret\n"
    assert not (outside / "new-host-file").exists()
    assert not (sd / "stat").is_symlink()
    assert not (sd / "uptime").is_symlink()


def test_refresh_does_not_hang_on_a_fifo(env):
    # Opening a pipe to compare its content would wait for a writer the
    # guest never provides.
    container, rootfs, _outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    sd = _sysdata(container)
    (sd / "stat").unlink()
    os.mkfifo(str(sd / "stat"))

    sysdata.setup_fake_sysdata(str(rootfs))

    assert stat.S_ISREG((sd / "stat").lstat().st_mode)
    assert (sd / "stat").read_text() == sysdata.fake_stat(
        sysdata.host_cpu_ids())


def test_refresh_steps_over_a_directory_in_the_way(env):
    container, rootfs, _outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    sd = _sysdata(container)
    (sd / "stat").unlink()
    (sd / "stat").mkdir()
    (sd / "stat" / "keep").write_text("guest data\n")

    sysdata.setup_fake_sysdata(str(rootfs))

    assert (sd / "stat" / "keep").read_text() == "guest data\n"
    assert (sd / "uptime").read_text() == sysdata.fake_uptime(
        sysdata.host_cpu_ids())
    # The failed rename took its temporary with it.
    assert [n for n in os.listdir(str(sd)) if ".tmp" in n] == []


def test_refresh_replaces_a_stale_file(env):
    container, rootfs, _outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    sd = _sysdata(container)
    _stale(sd)

    sysdata.setup_fake_sysdata(str(rootfs))

    assert (sd / "stat").read_text() == sysdata.fake_stat(
        sysdata.host_cpu_ids())
    assert (sd / "stat").stat().st_nlink == 1


# --- bindings --------------------------------------------------------------

def _sources(bindings):
    return [b.split("=", 1)[1].split(":", 1)[0] for b in bindings]


def test_bindings_name_only_validated_entries(env, monkeypatch):
    container, rootfs, _outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    # Force every /proc substitute to be considered unreadable so the
    # full set is emitted regardless of the host running the suite.
    monkeypatch.setattr(sysdata, "open", _unreadable, raising=False)

    bindings = sysdata.fake_sysdata_bindings(str(rootfs))
    sd = _sysdata(container)
    assert _sources(bindings) == (
        [str(sd / "sys_empty")]
        + [str(sd / name) for name, _r, _c in sysdata._FAKE_ENTRIES]
    )
    assert bindings[0].endswith(":/sys/fs/selinux")


def test_bindings_skip_a_planted_symlink(env, monkeypatch):
    container, rootfs, outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    monkeypatch.setattr(sysdata, "open", _unreadable, raising=False)

    sd = _sysdata(container)
    victim = outside / "secret"
    victim.write_text("host secret\n")
    (sd / "loadavg").unlink()
    os.symlink(str(victim), str(sd / "loadavg"))
    (sd / "sys_empty").rmdir()
    os.symlink(str(outside), str(sd / "sys_empty"))

    sources = _sources(sysdata.fake_sysdata_bindings(str(rootfs)))
    assert str(sd / "loadavg") not in sources
    assert str(sd / "sys_empty") not in sources
    assert str(sd / "stat") in sources


def test_bindings_skip_a_planted_hardlink(env, monkeypatch):
    # setup_fake_sysdata() is what normally drops one, but the binding
    # list is the half that decides what proot mounts, so it makes the
    # same judgement itself rather than trusting the pass before it.
    container, rootfs, outside = env
    sysdata.setup_fake_sysdata(str(rootfs))
    monkeypatch.setattr(sysdata, "open", _unreadable, raising=False)

    sd = _sysdata(container)
    victim = outside / "secret"
    victim.write_text("host secret\n")
    (sd / "loadavg").unlink()
    os.link(str(victim), str(sd / "loadavg"))

    sources = _sources(sysdata.fake_sysdata_bindings(str(rootfs)))
    assert str(sd / "loadavg") not in sources
    assert str(sd / "stat") in sources


def test_bindings_refuse_a_symlinked_sysdata_dir(env):
    container, rootfs, outside = env
    os.symlink(str(outside), str(_sysdata(container)))
    assert sysdata.fake_sysdata_bindings(str(rootfs)) == []


def _unreadable(*_args, **_kwargs):
    raise OSError("unreadable")
