# The fake /proc/stat has to list the CPUs the guest sees in /proc/cpuinfo.
# It was a fixed eight-core snapshot, so top/htop/nproc-style tools on a
# phone with any other core count read two files that disagreed -- and a
# container written once kept the wrong count for good, since the stub was
# only created when missing.

import os
import re

import pytest

from proot_distro import sysdata


def _cpuinfo(path, ids, *, style="arm"):
    """Write a cpuinfo naming *ids*, shaped like a real kernel's."""
    blocks = []
    for cpu in ids:
        if style == "x86":
            blocks.append(
                f"processor\t: {cpu}\nvendor_id\t: GenuineIntel\n"
                f"model name\t: Fake processor 9000\ncpu cores\t: {len(ids)}\n"
            )
        else:
            blocks.append(
                f"processor\t: {cpu}\nBogoMIPS\t: 38.40\n"
                f"Features\t: fp asimd evtstrm\nCPU implementer\t: 0x41\n"
            )
    path.write_text("\n".join(blocks) + "\nHardware\t: Fake SoC\n")
    return path


def _cpu_lines(stat_text):
    return re.findall(r"^cpu(\d+) ", stat_text, re.M)


def _counters(line):
    return [int(x) for x in line.split()[1:]]


@pytest.fixture
def container(tmp_path):
    root = tmp_path / "container"
    (root / "rootfs").mkdir(parents=True)
    return root


# --- host_cpu_ids ----------------------------------------------------------

@pytest.mark.parametrize("style", ["arm", "x86"])
def test_ids_come_from_the_processor_lines(tmp_path, monkeypatch, style):
    path = _cpuinfo(tmp_path / "cpuinfo", range(6), style=style)
    monkeypatch.setattr(sysdata, "_CPUINFO", str(path))
    assert sysdata.host_cpu_ids() == [0, 1, 2, 3, 4, 5]


def test_ids_keep_the_gap_of_an_offline_core(tmp_path, monkeypatch):
    # An Android kernel leaves a switched-off core out of both files, so
    # the numbering has a hole that counting the lines would close.
    path = _cpuinfo(tmp_path / "cpuinfo", [0, 1, 3, 6])
    monkeypatch.setattr(sysdata, "_CPUINFO", str(path))
    assert sysdata.host_cpu_ids() == [0, 1, 3, 6]


def test_ids_ignore_lines_that_only_mention_a_processor(tmp_path, monkeypatch):
    path = tmp_path / "cpuinfo"
    path.write_text(
        "processor\t: 0\nmodel name\t: processor : 7\n"
        "processor\t: x\nprocessor\t: -1\nprocessor\t: 1\n"
        "processor\t: 1\n"
    )
    monkeypatch.setattr(sysdata, "_CPUINFO", str(path))
    assert sysdata.host_cpu_ids() == [0, 1]


@pytest.mark.parametrize("content", [None, "", "Hardware\t: nothing\n"])
def test_ids_fall_back_to_the_cpu_count(tmp_path, monkeypatch, content):
    # No file, an empty one, or a format with no `processor : N` lines.
    path = tmp_path / "cpuinfo"
    if content is not None:
        path.write_text(content)
    monkeypatch.setattr(sysdata, "_CPUINFO", str(path))
    monkeypatch.setattr(os, "cpu_count", lambda: 4)
    assert sysdata.host_cpu_ids() == [0, 1, 2, 3]


def test_ids_are_never_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(sysdata, "_CPUINFO", str(tmp_path / "missing"))
    monkeypatch.setattr(os, "cpu_count", lambda: None)
    assert sysdata.host_cpu_ids() == [0]


# --- fake_stat / fake_uptime -----------------------------------------------

@pytest.mark.parametrize("count", [1, 2, 3, 8, 9, 13, 64])
def test_stat_has_one_line_per_cpu(count):
    ids = list(range(count))
    assert _cpu_lines(sysdata.fake_stat(ids)) == [str(i) for i in ids]


def test_stat_names_the_ids_it_is_given():
    assert _cpu_lines(sysdata.fake_stat([0, 2, 5])) == ["0", "2", "5"]


@pytest.mark.parametrize("count", [1, 3, 8, 13])
def test_stat_aggregate_is_the_sum_of_the_cpus(count):
    lines = sysdata.fake_stat(list(range(count))).splitlines()
    per_cpu = [_counters(ln) for ln in lines if re.match(r"cpu\d", ln)]
    (aggregate,) = [ln for ln in lines if ln.startswith("cpu ")]
    assert _counters(aggregate) == [sum(col) for col in zip(*per_cpu)]


def test_stat_keeps_the_kernel_shape():
    text = sysdata.fake_stat([0, 1])
    lines = text.splitlines()
    assert lines[0].startswith("cpu  ")             # two spaces, as the kernel
    assert all(len(_counters(ln)) == 10 for ln in lines[:3])
    for key in ("intr", "ctxt", "btime", "processes", "procs_running",
                "procs_blocked", "softirq"):
        assert len([ln for ln in lines if ln.startswith(key + " ")]) == 1
    assert text.endswith("\n")


def test_stat_tail_does_not_depend_on_the_cpu_count():
    def tail(ids):
        text = sysdata.fake_stat(ids)
        return text[text.index("intr "):]
    assert tail([0]) == tail(list(range(32)))


def test_stat_tolerates_an_empty_list():
    # host_cpu_ids() never returns one; a malformed "cpu " line would be
    # the price of assuming that.
    assert _cpu_lines(sysdata.fake_stat([])) == ["0"]
    assert sysdata.fake_uptime([]) == sysdata.fake_uptime([0])


@pytest.mark.parametrize("count", [1, 4, 8, 12])
def test_uptime_idle_agrees_with_stat(count):
    ids = list(range(count))
    aggregate = next(ln for ln in sysdata.fake_stat(ids).splitlines()
                     if ln.startswith("cpu "))
    uptime, idle = sysdata.fake_uptime(ids).split()
    assert float(idle) == _counters(aggregate)[3] / 100
    # No CPU can have been idle for longer than the machine has been up.
    assert float(idle) <= float(uptime) * count


# --- setup against a cpuinfo -----------------------------------------------

def test_setup_writes_the_cpus_of_cpuinfo(container, tmp_path, monkeypatch):
    # The regression: stat listed cpu0..cpu7 whatever cpuinfo said.
    info = _cpuinfo(tmp_path / "cpuinfo", range(4))
    monkeypatch.setattr(sysdata, "_CPUINFO", str(info))

    sysdata.setup_fake_sysdata(str(container / "rootfs"))

    sd = container / "sysdata"
    assert _cpu_lines((sd / "stat").read_text()) == ["0", "1", "2", "3"]
    assert (sd / "uptime").read_text() == sysdata.fake_uptime(range(4))


def test_setup_follows_a_cpu_count_that_changed(container, tmp_path,
                                                monkeypatch):
    # Cores go offline and come back; a container directory is restored
    # onto another device. The next session must not keep the old count.
    info = tmp_path / "cpuinfo"
    monkeypatch.setattr(sysdata, "_CPUINFO", str(info))
    rootfs = str(container / "rootfs")
    sd = container / "sysdata"

    _cpuinfo(info, range(8))
    sysdata.setup_fake_sysdata(rootfs)
    assert len(_cpu_lines((sd / "stat").read_text())) == 8

    _cpuinfo(info, [0, 1, 2, 3, 4, 5])
    sysdata.setup_fake_sysdata(rootfs)
    assert _cpu_lines((sd / "stat").read_text()) == list("012345")
    assert (sd / "uptime").read_text() == sysdata.fake_uptime(range(6))

    _cpuinfo(info, [0, 4, 5, 7, 9])
    sysdata.setup_fake_sysdata(rootfs)
    assert _cpu_lines((sd / "stat").read_text()) == ["0", "4", "5", "7", "9"]


def test_setup_replaces_a_stat_written_by_an_older_version(container, tmp_path,
                                                           monkeypatch):
    # Containers created before the fix hold the fixed eight-core text.
    info = _cpuinfo(tmp_path / "cpuinfo", range(2))
    monkeypatch.setattr(sysdata, "_CPUINFO", str(info))
    sd = container / "sysdata"
    sd.mkdir()
    (sd / "stat").write_text(
        "cpu  1957 0 2877 93280 262 342 254 87 0 0\n"
        + "".join(f"cpu{i} 1 0 1 1 1 1 1 1 0 0\n" for i in range(8))
    )
    (sd / "uptime").write_text("124.08 932.80\n")

    sysdata.setup_fake_sysdata(str(container / "rootfs"))

    assert _cpu_lines((sd / "stat").read_text()) == ["0", "1"]
    assert (sd / "uptime").read_text() == sysdata.fake_uptime([0, 1])


def test_setup_leaves_current_content_alone(container, tmp_path, monkeypatch):
    # Rewriting on every login would churn the inode and mtime of a file
    # a running session has mounted, for no change.
    info = _cpuinfo(tmp_path / "cpuinfo", range(4))
    monkeypatch.setattr(sysdata, "_CPUINFO", str(info))
    rootfs = str(container / "rootfs")
    sysdata.setup_fake_sysdata(rootfs)
    sd = container / "sysdata"
    before = {n: (sd / n).stat() for n in ("stat", "uptime")}

    sysdata.setup_fake_sysdata(rootfs)

    for name, st in before.items():
        now = (sd / name).stat()
        assert (now.st_ino, now.st_mtime_ns) == (st.st_ino, st.st_mtime_ns)


def test_setup_leaves_no_temporaries(container, tmp_path, monkeypatch):
    info = tmp_path / "cpuinfo"
    monkeypatch.setattr(sysdata, "_CPUINFO", str(info))
    rootfs = str(container / "rootfs")
    for ids in (range(8), range(3), range(3), range(5)):
        _cpuinfo(info, ids)
        sysdata.setup_fake_sysdata(rootfs)

    expected = {"sys_empty"} | {n for n, _r, _c in sysdata._FAKE_ENTRIES}
    assert set(os.listdir(container / "sysdata")) == expected


def test_stat_still_binds_over_an_unreadable_proc_stat(container, tmp_path,
                                                       monkeypatch):
    info = _cpuinfo(tmp_path / "cpuinfo", range(3))
    monkeypatch.setattr(sysdata, "_CPUINFO", str(info))
    sysdata.setup_fake_sysdata(str(container / "rootfs"))

    def unreadable(*_a, **_k):
        raise OSError("unreadable")
    monkeypatch.setattr(sysdata, "open", unreadable, raising=False)

    bindings = sysdata.fake_sysdata_bindings(str(container / "rootfs"))
    assert f"--bind={container / 'sysdata' / 'stat'}:/proc/stat" in bindings
