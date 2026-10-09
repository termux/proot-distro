#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

# Architecture: Supplies fake /proc and /sys content that proot bind-mounts
# over the real entries in the container. Android restricts or blocks
# several /proc files; providing static replacements ensures distro tools
# that read them (top, htop, etc.) work correctly. The fake files live
# under containers/<name>/sysdata/ so they are removed together with the
# container.
#
# That directory is guest-writable: on Termux it sits under $TERMUX_PREFIX,
# which is bound read-write into every non-isolated container, so a session
# can replace any entry here between runs. Every name below is therefore
# addressed as (directory fd, entry name) with O_NOFOLLOW, never as a path:
# os.path.exists() plus open(path, "w") followed a planted symlink and
# created -- or, for a name whose target already existed, silently skipped
# -- a host file, os.makedirs(exist_ok=True) accepted one in place of
# sys_empty, and os.chmod() applied 0700 to whatever it led to. The bind
# arguments are just as bad the other way round: proot mounts what the name
# resolves to, so a symlinked `loadavg` would have handed the guest a host
# file as /proc/loadavg. An entry that is not of the type this module wrote
# is dropped and remade -- nothing else writes here, so nothing legitimate
# is lost -- and one that cannot be validated is left unbound rather than
# followed.
#
# "The type this module wrote" has to include the link count, because a
# hardlink is a regular file: it *is* another file, under a second name,
# and O_NOFOLLOW has nothing to refuse. A session that unlinks sysdata/
# loadavg and links a host file into its place leaves an entry every later
# session accepts as its own -- setup_fake_sysdata() keeps it, and
# fake_sysdata_bindings() names it as the source proot mounts at
# /proc/loadavg, where the guest can read *and write* it, since proot has
# no read-only bind. That is the persistent case this module exists to
# rule out, so a file this module wrote is one with exactly one link, and
# anything else is dropped and written again.

import os
import stat

from proot_distro import dirfd
from proot_distro.constants import (
    DEFAULT_FAKE_KERNEL_RELEASE,
    DEFAULT_FAKE_KERNEL_VERSION,
)

_FAKE_LOADAVG = "0.12 0.07 0.02 2/165 765\n"

_FAKE_OVERFLOW_ID = "65534\n"

# One snapshot row per CPU: the ten counters of a /proc/stat "cpuN" line
# (user nice system idle iowait irq softirq steal guest guest_nice) in
# USER_HZ ticks, each CPU about 120 s into a 124 s uptime. fake_stat()
# lays them over however many CPUs the host has, cycling through the rows
# when it has more than eight.
_FAKE_CPU_ROWS = (
    (31, 0, 226, 12027, 82, 10, 4, 9, 0, 0),
    (45, 0, 664, 11144, 21, 263, 233, 12, 0, 0),
    (494, 0, 537, 11283, 27, 10, 3, 8, 0, 0),
    (359, 0, 234, 11723, 24, 26, 5, 7, 0, 0),
    (295, 0, 268, 11772, 10, 12, 2, 12, 0, 0),
    (270, 0, 251, 11833, 15, 3, 1, 10, 0, 0),
    (430, 0, 520, 11386, 30, 8, 1, 12, 0, 0),
    (30, 0, 172, 12108, 50, 8, 1, 13, 0, 0),
)
_IDLE_COLUMN = 3
_USER_HZ = 100
_FAKE_UPTIME_SECONDS = "124.08"

# The counters after the per-CPU lines. None of them depends on how many
# CPUs there are.
_FAKE_STAT_TAIL = """\
intr 127541 38 290 0 0 0 0 4 0 1 0 0 25329 258 0 5777 277 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
ctxt 140223
btime 1680020856
processes 772
procs_running 2
procs_blocked 0
softirq 75663 0 5903 6 25375 10774 0 243 11685 0 21677
"""

# What the guest's /proc/cpuinfo is read from. The host's: nothing here
# fakes it, so it is the file fake_stat() has to agree with.
_CPUINFO = "/proc/cpuinfo"


def host_cpu_ids() -> list:
    """Return the sorted ids of the CPUs /proc/cpuinfo lists.

    The kernel lists the online CPUs there, by the same ids its real
    /proc/stat uses for ``cpuN`` -- on Android a core that is switched
    off disappears from both, leaving a gap in the numbering that a
    ``range()`` would paper over. A kernel whose cpuinfo has no
    ``processor : N`` lines (some architectures format it differently),
    or a file that cannot be read, falls back to ``os.cpu_count()``, and
    to one CPU when even that is unknown, so the list is never empty.
    """
    ids = set()
    try:
        with open(_CPUINFO, "rb") as fh:
            for line in fh:
                key, sep, value = line.partition(b":")
                value = value.strip()
                if sep and key.strip() == b"processor" and value.isdigit():
                    ids.add(int(value))
    except OSError:
        pass
    if not ids:
        ids = set(range(os.cpu_count() or 1))
    return sorted(ids)


def _cpu_rows(cpus) -> list:
    """Pair each id in *cpus* (CPU 0 when empty) with its snapshot counters."""
    return [(cpu, _FAKE_CPU_ROWS[n % len(_FAKE_CPU_ROWS)])
            for n, cpu in enumerate(cpus or [0])]


def fake_stat(cpus) -> str:
    """Return /proc/stat content with one ``cpuN`` line per id in *cpus*.

    The aggregate ``cpu`` line is the column-wise sum of those lines, as
    the kernel's is, so a tool that cross-checks the two (or divides one
    by the other) sees them agree for any number of CPUs.
    """
    rows = _cpu_rows(cpus)
    total = [sum(column) for column in zip(*(row for _cpu, row in rows))]
    lines = ["cpu  " + " ".join(map(str, total))]
    for cpu, row in rows:
        lines.append(f"cpu{cpu} " + " ".join(map(str, row)))
    return "\n".join(lines) + "\n" + _FAKE_STAT_TAIL


def fake_uptime(cpus) -> str:
    """Return /proc/uptime content consistent with fake_stat(*cpus*).

    The second field is the idle time summed over every CPU, so it is
    taken from the same rows as the idle column of ``cpu`` in /proc/stat.
    """
    idle = sum(row[_IDLE_COLUMN] for _cpu, row in _cpu_rows(cpus))
    return f"{_FAKE_UPTIME_SECONDS} {idle / _USER_HZ:.2f}\n"


_FAKE_VMSTAT = """\
nr_free_pages 1743136
nr_zone_inactive_anon 179281
nr_zone_active_anon 7183
nr_zone_inactive_file 22858
nr_zone_active_file 51328
nr_zone_unevictable 642
nr_zone_write_pending 0
nr_mlock 0
nr_bounce 0
nr_zspages 0
nr_free_cma 0
numa_hit 1259626
numa_miss 0
numa_foreign 0
numa_interleave 720
numa_local 1259626
numa_other 0
nr_inactive_anon 179281
nr_active_anon 7183
nr_inactive_file 22858
nr_active_file 51328
nr_unevictable 642
nr_slab_reclaimable 8091
nr_slab_unreclaimable 7804
nr_isolated_anon 0
nr_isolated_file 0
workingset_nodes 0
workingset_refault_anon 0
workingset_refault_file 0
workingset_activate_anon 0
workingset_activate_file 0
workingset_restore_anon 0
workingset_restore_file 0
workingset_nodereclaim 0
nr_anon_pages 7723
nr_mapped 8905
nr_file_pages 253569
nr_dirty 0
nr_writeback 0
nr_writeback_temp 0
nr_shmem 178741
nr_shmem_hugepages 0
nr_shmem_pmdmapped 0
nr_file_hugepages 0
nr_file_pmdmapped 0
nr_anon_transparent_hugepages 1
nr_vmscan_write 0
nr_vmscan_immediate_reclaim 0
nr_dirtied 0
nr_written 0
nr_throttled_written 0
nr_kernel_misc_reclaimable 0
nr_foll_pin_acquired 0
nr_foll_pin_released 0
nr_kernel_stack 2780
nr_page_table_pages 344
nr_sec_page_table_pages 0
nr_swapcached 0
pgpromote_success 0
pgpromote_candidate 0
nr_dirty_threshold 356564
nr_dirty_background_threshold 178064
pgpgin 890508
pgpgout 0
pswpin 0
pswpout 0
pgalloc_dma 272
pgalloc_dma32 261
pgalloc_normal 1328079
pgalloc_movable 0
pgalloc_device 0
allocstall_dma 0
allocstall_dma32 0
allocstall_normal 0
allocstall_movable 0
allocstall_device 0
pgskip_dma 0
pgskip_dma32 0
pgskip_normal 0
pgskip_movable 0
pgskip_device 0
pgfree 3077011
pgactivate 0
pgdeactivate 0
pglazyfree 0
pgfault 176973
pgmajfault 488
pglazyfreed 0
pgrefill 0
pgreuse 19230
pgsteal_kswapd 0
pgsteal_direct 0
pgsteal_khugepaged 0
pgdemote_kswapd 0
pgdemote_direct 0
pgdemote_khugepaged 0
pgscan_kswapd 0
pgscan_direct 0
pgscan_khugepaged 0
pgscan_direct_throttle 0
pgscan_anon 0
pgscan_file 0
pgsteal_anon 0
pgsteal_file 0
zone_reclaim_failed 0
pginodesteal 0
slabs_scanned 0
kswapd_inodesteal 0
kswapd_low_wmark_hit_quickly 0
kswapd_high_wmark_hit_quickly 0
pageoutrun 0
pgrotated 0
drop_pagecache 0
drop_slab 0
oom_kill 0
numa_pte_updates 0
numa_huge_pte_updates 0
numa_hint_faults 0
numa_hint_faults_local 0
numa_pages_migrated 0
pgmigrate_success 0
pgmigrate_fail 0
thp_migration_success 0
thp_migration_fail 0
thp_migration_split 0
compact_migrate_scanned 0
compact_free_scanned 0
compact_isolated 0
compact_stall 0
compact_fail 0
compact_success 0
compact_daemon_wake 0
compact_daemon_migrate_scanned 0
compact_daemon_free_scanned 0
htlb_buddy_alloc_success 0
htlb_buddy_alloc_fail 0
cma_alloc_success 0
cma_alloc_fail 0
unevictable_pgs_culled 27002
unevictable_pgs_scanned 0
unevictable_pgs_rescued 744
unevictable_pgs_mlocked 744
unevictable_pgs_munlocked 744
unevictable_pgs_cleared 0
unevictable_pgs_stranded 0
thp_fault_alloc 13
thp_fault_fallback 0
thp_fault_fallback_charge 0
thp_collapse_alloc 4
thp_collapse_alloc_failed 0
thp_file_alloc 0
thp_file_fallback 0
thp_file_fallback_charge 0
thp_file_mapped 0
thp_split_page 0
thp_split_page_failed 0
thp_deferred_split_page 1
thp_split_pmd 1
thp_scan_exceed_none_pte 0
thp_scan_exceed_swap_pte 0
thp_scan_exceed_share_pte 0
thp_split_pud 0
thp_zero_page_alloc 0
thp_zero_page_alloc_failed 0
thp_swpout 0
thp_swpout_fallback 0
balloon_inflate 0
balloon_deflate 0
balloon_migrate 0
swap_ra 0
swap_ra_hit 0
ksm_swpin_copy 0
cow_ksm 0
zswpin 0
zswpout 0
direct_map_level2_splits 29
direct_map_level3_splits 0
nr_unstable 0
"""

_FAKE_VERSION = (
    f"Linux version {DEFAULT_FAKE_KERNEL_RELEASE} (proot@termux) "
    f"(gcc (GCC) 13.3.0, GNU ld (GNU Binutils) 2.42) "
    f"{DEFAULT_FAKE_KERNEL_VERSION}\n"
)

# The fake /proc entries, in the order they are written and bound: the
# name under sysdata/, the guest path it substitutes for, and the content.
# The content is a string, written once and then left alone, or -- for the
# entries that describe the CPUs -- a function of the host's CPU ids,
# which is rewritten whenever its output changes (see setup_fake_sysdata).
_FAKE_ENTRIES = (
    ("loadavg", "/proc/loadavg", _FAKE_LOADAVG),
    ("stat", "/proc/stat", fake_stat),
    ("uptime", "/proc/uptime", fake_uptime),
    ("version", "/proc/version", _FAKE_VERSION),
    ("vmstat", "/proc/vmstat", _FAKE_VMSTAT),
    ("sysctl_entry_cap_last_cap", "/proc/sys/kernel/cap_last_cap", "40\n"),
    ("sysctl_inotify_max_user_watches",
     "/proc/sys/fs/inotify/max_user_watches", "4096\n"),
    ("sysctl_kernel_overflowuid", "/proc/sys/kernel/overflowuid",
     _FAKE_OVERFLOW_ID),
    ("sysctl_kernel_overflowgid", "/proc/sys/kernel/overflowgid",
     _FAKE_OVERFLOW_ID),
)


def sysdata_dir(rootfs: str) -> str:
    """Path of the sysdata directory belonging to *rootfs*."""
    return os.path.join(os.path.dirname(rootfs), "sysdata")


def _ensure_dir_at(dir_fd: int, name: str, mode: int = None):
    """Open the subdirectory *name*, creating it. Descriptor, or None.

    O_NOFOLLOW throughout, so a symlink under the name is refused rather
    than followed. Anything that is refused is then unlinked and the
    directory made for real: this tree is written by nothing but the code
    below, so an entry of the wrong type was planted, and leaving it
    would keep the fake data permanently unavailable as well as unsafe.
    *mode* is applied to the descriptor, never to the name.
    """
    fd = None
    try:
        fd = dirfd.opendir_at(dir_fd, name)
    except FileNotFoundError:
        pass
    except OSError:
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            return None
    if fd is None:
        try:
            os.mkdir(name, 0o755, dir_fd=dir_fd)
        except FileExistsError:
            pass                    # lost a race with another writer
        except OSError:
            return None
        try:
            fd = dirfd.opendir_at(dir_fd, name)
        except OSError:
            return None
    if mode is not None:
        try:
            os.fchmod(fd, mode)
        except OSError:
            pass
    return fd


def _is_own_file(st) -> bool:
    """True when *st* describes a file this module could have written.

    A plain file with exactly one link. The link count is half the test
    because a hardlink is not a distinct kind of entry -- it is the file
    itself under a second name, so S_ISREG alone accepts one a guest made
    to a host file and every later session then treats that inode as its
    own fake /proc content. Nothing here ever creates a second link to
    anything it writes, so st_nlink != 1 means the entry was planted.
    """
    return stat.S_ISREG(st.st_mode) and st.st_nlink == 1


def _write_if_missing(dir_fd: int, name: str, content: str) -> None:
    """Create *name* under dir_fd with *content*, unless already present.

    "Present" means a plain file with one link: an entry of any other
    type -- or one that is also linked from somewhere else -- is not one
    this module wrote, so it is removed and the real file created in its
    place. O_CREAT|O_EXCL|O_NOFOLLOW then guarantees the bytes go into a
    new inode inside this directory and nowhere else. Unlinking the name
    leaves whatever else the inode is linked from untouched, which is the
    point: a host file that was linked here keeps its content and simply
    stops being this container's /proc/loadavg.
    """
    try:
        st = dirfd.lstat_at(dir_fd, name)
    except FileNotFoundError:
        st = None
    except OSError:
        return
    if st is not None:
        if _is_own_file(st):
            return
        try:
            os.unlink(name, dir_fd=dir_fd)
        except OSError:
            return

    try:
        fd = dirfd.open_file_at(
            dir_fd, name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644,
        )
    except OSError:
        return
    try:
        data = content.encode()
        while data:
            data = data[os.write(fd, data):]
    except OSError:
        pass
    finally:
        os.close(fd)


def _holds(dir_fd: int, name: str, data: bytes) -> bool:
    """True when *name* is a file this module wrote, containing *data*."""
    try:
        fd, st = dirfd.open_regular_at(dir_fd, name, os.O_RDONLY)
    except OSError:
        return False
    try:
        with os.fdopen(fd, "rb") as fh:
            fd = None
            return (_is_own_file(st) and st.st_size == len(data)
                    and fh.read(len(data) + 1) == data)
    except OSError:
        return False
    finally:
        if fd is not None:
            os.close(fd)


def _write_current(dir_fd: int, name: str, content: str) -> None:
    """Make *name* under dir_fd a plain file holding exactly *content*.

    For the entries whose content follows the host (see fake_stat), which
    unlike _write_if_missing() cannot leave an existing file alone: the
    CPU count it was written for may no longer be the host's -- an Android
    kernel takes cores offline and brings them back, and a container's
    directory can be restored onto another device. A file that already
    holds *content* is not touched.

    Otherwise the new content goes into a fresh O_EXCL temporary in the
    same directory and is renamed over the name. rename(2) replaces the
    directory entry and follows nothing at the destination, so a symlink
    or a hardlink planted as *name* is swapped out rather than written
    through, with no window in which the entry is missing or half-written
    for a session starting at the same moment. A directory in the way
    makes the rename fail; it is left alone, as _write_if_missing() does.
    """
    data = content.encode()
    if _holds(dir_fd, name, data):
        return
    tmp = dirfd.temp_name(name, f".{os.getpid()}.{os.urandom(4).hex()}.tmp")
    try:
        fd, _st = dirfd.open_new_at(dir_fd, tmp, 0o644)
    except OSError:
        return
    try:
        try:
            view = data
            while view:
                view = view[os.write(fd, view):]
        finally:
            os.close(fd)
        os.replace(tmp, name, src_dir_fd=dir_fd, dst_dir_fd=dir_fd)
    except OSError:
        pass
    finally:
        # Nothing left under the temporary's name after a rename, and a
        # failed write or rename (or an interrupt) must not leave it.
        dirfd.unlink_quietly(dir_fd, tmp)


def setup_fake_sysdata(rootfs: str, *, container_fd=None) -> None:
    """Create fake /proc and /sys stubs required by proot on Android.

    *rootfs* is the absolute path to the container's rootfs directory
    (e.g. ``$RUNTIME_DIR/containers/<name>/rootfs``).  Fake files are
    written to a sibling ``sysdata/`` directory, not into the rootfs.
    Most are written once; ``stat`` and ``uptime`` are regenerated for
    the host's current CPU list, so ``/proc/stat`` always has the same
    ``cpuN`` lines as the ``/proc/cpuinfo`` the guest reads beside it.

    *container_fd* is that sibling's parent -- `containers/<name>` --
    when the caller has pinned it, and a caller that has one must pass
    it. Everything below here is already named as (dir_fd, entry), but
    the walk started at `os.path.dirname(rootfs)`, a name that is
    guest-writable on Termux and that `login` resolves while holding
    only a shared lock. This directory is *created* and chmod'ed 0700
    and its files written, so a re-pointed name meant doing all of that
    somewhere else.
    """
    try:
        parent_fd = (dirfd.reopen(container_fd) if container_fd is not None
                     else dirfd.opendir(os.path.dirname(rootfs)))
    except OSError:
        return
    try:
        dir_fd = _ensure_dir_at(parent_fd, "sysdata", mode=0o700)
    finally:
        os.close(parent_fd)
    if dir_fd is None:
        return

    try:
        empty_fd = _ensure_dir_at(dir_fd, "sys_empty")
        if empty_fd is not None:
            os.close(empty_fd)
        cpus = host_cpu_ids()
        for name, _real, content in _FAKE_ENTRIES:
            if callable(content):
                _write_current(dir_fd, name, content(cpus))
            else:
                _write_if_missing(dir_fd, name, content)
    finally:
        os.close(dir_fd)


def _is_own_dir(st) -> bool:
    """True when *st* describes a plain directory.

    No link-count test: a directory cannot be hardlinked, so the type is
    the whole of it.
    """
    return stat.S_ISDIR(st.st_mode)


def _accepts_at(dir_fd: int, name: str, predicate) -> bool:
    """True when *name* under dir_fd is an entry *predicate* accepts."""
    try:
        st = dirfd.lstat_at(dir_fd, name)
    except OSError:
        return False
    return predicate(st)


def _open_sysdata_dir(rootfs: str, container_fd):
    """Open the container's sysdata/ directory. Descriptor, or None.

    From the pinned container directory when the caller has one; see
    setup_fake_sysdata for why that matters.
    """
    if container_fd is None:
        return dirfd.opendir_under(os.path.dirname(rootfs), ("sysdata",))
    try:
        return dirfd.descend_at(container_fd, ("sysdata",))
    except OSError:
        return None


def fake_sysdata_bindings(rootfs: str, *, container_fd=None) -> list:
    """Return --bind args for the fake /proc and /sys entries of *rootfs*.

    A /proc entry the host can already read is left alone, so a stub
    stands in only where the real file is blocked or missing.

    Only entries this module could verify are bound: sys_empty as a real
    directory, each /proc substitute as a plain file with one link -- a
    second link means the inode is reachable under some other name too,
    which is exactly how a guest hands a host file to the next session as
    its /proc/loadavg (see _is_own_file). A directory needs no such test:
    it cannot be hardlinked. proot still resolves the source by name when
    it mounts it, so a session running against the same container can
    re-point one in between; what the checks remove is the persistent
    case, where a guest leaves a symlink or a link to a host file behind
    and every later session mounts it into the guest.
    """
    base = sysdata_dir(rootfs)
    dir_fd = _open_sysdata_dir(rootfs, container_fd)
    if dir_fd is None:
        return []

    bindings = []
    try:
        if _accepts_at(dir_fd, "sys_empty", _is_own_dir):
            bindings.append(f"--bind={base}/sys_empty:/sys/fs/selinux")
        for name, real, _content in _FAKE_ENTRIES:
            try:
                with open(real, "rb") as fh:
                    fh.read(1)
                continue            # the real entry is readable as it is
            except OSError:
                pass
            if _accepts_at(dir_fd, name, _is_own_file):
                bindings.append(f"--bind={os.path.join(base, name)}:{real}")
    finally:
        os.close(dir_fd)
    return bindings
