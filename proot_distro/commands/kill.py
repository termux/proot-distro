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

# Architecture: Reliable teardown of a container session's entire process
# tree, surfaced by the `kill` command and anchored to the session
# registry (session.py) that `ps` reads.
#
# Why a tree walk: a session's recorded PID is the root proot, which acts
# as the ptrace tracer of the whole guest. Its descendants in the host PID
# namespace are exactly the guest processes. Signalling only the root is
# not enough — proot's `--kill-on-exit` cleanup runs only on a *graceful*
# exit, so `kill -9 <proot>` orphans the guest daemons; and on non-Termux
# hosts `--kill-on-exit` is never set at all, so even a graceful kill of
# proot leaves children running. We therefore read /proc, build a
# pid -> ppid map, collect the transitive closure of children under each
# target session's root, and os.kill() every member (pure-Python — no
# pkill/pgrep/kill(1)).
#
# Targets are always resolved against active_sessions(), so `kill` can
# only ever signal tracked proot sessions, never an arbitrary host PID.
# As a PID-reuse safety belt, a root is walked only when /proc/<root>/comm
# still reads as "proot".

import os
import signal
import sys

from proot_distro.constants import PROGRAM_NAME
from proot_distro.message import msg, log_info, warn, crit_error
from proot_distro.names import require_valid_name
from proot_distro.session import active_sessions


def command_kill(args) -> None:
    """Signal the full process tree of one or more active sessions."""
    target = getattr(args, "target", None)
    kill_all = getattr(args, "all", False)
    signal_name = getattr(args, "signal", None)

    sig = _parse_signal(signal_name) if signal_name else signal.SIGTERM

    if kill_all and target is not None:
        _fail("a target cannot be combined with --all.")
    if not kill_all and target is None:
        _fail("specify a PID, a container name, or --all.")

    sessions = active_sessions()
    selected = _select_sessions(sessions, target, kill_all)
    if not selected:
        log_info(_no_match_message(target, kill_all))
        return

    delivered, roots = _signal_sessions(selected, sig)
    if not roots:
        log_info("No live session processes to signal.")
        return

    log_info(
        f"Sent {_signal_label(sig)} to {delivered} "
        f"process(es) across {roots} session(s)."
    )


def _select_sessions(sessions, target, kill_all):
    """Resolve the requested target to a list of session records."""
    if kill_all:
        return list(sessions)
    if target.isdigit():
        # A bare number is always interpreted as a PID (as shown by `ps`),
        # even though container names may also be all-digits.
        pid = int(target)
        return [s for s in sessions if s.get("pid") == pid]
    require_valid_name(target)
    return [s for s in sessions if s.get("container") == target]


def _no_match_message(target, kill_all):
    """Friendly explanation when nothing matched the requested target."""
    if kill_all:
        return "No active sessions."
    if target.isdigit():
        return f"No active session with PID {int(target)}."
    return f"No active sessions for container '{target}'."


def _signal_sessions(selected, sig):
    """Collect and signal the process tree of each selected session.

    Returns (delivered_count, roots_used). A first pass signals the tree
    captured up front; a second pass re-reads /proc and signals anything
    spawned during the first pass.
    """
    roots = []
    for sess in selected:
        root = sess.get("pid")
        if not isinstance(root, int):
            continue
        state = _root_is_proot(root)
        if state is None:
            continue  # /proc entry gone -> already dead, nothing to do
        if state is False:
            warn(f"PID {root} is no longer a {PROGRAM_NAME} session; "
                 f"skipping.")
            continue
        roots.append(root)

    if not roots:
        return 0, 0

    delivered = _signal_pass(roots, sig)
    _signal_pass(roots, sig)  # sweep stragglers forked during the first pass
    return delivered, len(roots)


def _signal_pass(roots, sig):
    """Read /proc, collect every root's tree, and signal the union once."""
    pid_ppid = _read_pid_ppid()
    tree = set()
    for root in roots:
        tree |= _collect_tree(root, pid_ppid)

    self_pid = os.getpid()
    delivered = 0
    for pid in sorted(tree):
        if pid in (0, 1) or pid == self_pid:
            continue
        try:
            os.kill(pid, sig)
            delivered += 1
        except ProcessLookupError:
            pass  # exited between enumeration and the signal
        except PermissionError:
            warn(f"no permission to signal PID {pid}.")
    return delivered


def _read_pid_ppid():
    """Best-effort {pid: ppid} for every process on the host.

    Parses /proc/<pid>/status 'PPid:' (the same source cli uses for its
    nested-proot check). Any unreadable entry is skipped. A host without
    /proc yields an empty map, which degrades the tree walk to signalling
    only the recorded root PIDs.
    """
    result = {}
    try:
        names = os.listdir("/proc")
    except OSError:
        return result
    for name in names:
        if not name.isdigit():
            continue
        try:
            with open(f"/proc/{name}/status") as fh:
                for line in fh:
                    if line.startswith("PPid:"):
                        result[int(name)] = int(line.split()[1])
                        break
        except (OSError, ValueError):
            continue
    return result


def _collect_tree(root, pid_ppid):
    """Return *root* plus all of its transitive descendants.

    Pure given *pid_ppid* (a {pid: ppid} map). Walks iteratively with a
    'seen' set so self-references or cycles in the map cannot loop
    forever. *root* itself is always included, even when it has no
    children or is absent from the map.
    """
    children = {}
    for pid, ppid in pid_ppid.items():
        children.setdefault(ppid, set()).add(pid)

    seen = set()
    stack = [root]
    while stack:
        cur = stack.pop()
        if cur in seen:
            continue
        seen.add(cur)
        for child in children.get(cur, ()):
            if child not in seen:
                stack.append(child)
    return seen


def _root_is_proot(pid):
    """Return True if /proc/<pid>/comm is 'proot', False on a mismatch,
    None when the entry is unreadable (the process is already gone)."""
    try:
        with open(f"/proc/{pid}/comm") as fh:
            return fh.read().strip() == "proot"
    except OSError:
        return None


def _parse_signal(name):
    """Resolve a user-supplied signal ('TERM', 'SIGTERM', '9') to a number.

    Exits with an error on an unknown signal name or number.
    """
    raw = (name or "").strip().upper()
    if not raw:
        _fail("signal name cannot be empty.")
    if raw.isdigit():
        try:
            return signal.Signals(int(raw))
        except ValueError:
            _fail(f"invalid signal number '{name}'.")
    if not raw.startswith("SIG"):
        raw = "SIG" + raw
    try:
        return signal.Signals[raw]
    except KeyError:
        _fail(f"invalid signal name '{name}'.")


def _signal_label(sig):
    """Human-readable signal name for reporting (e.g. 'SIGKILL')."""
    try:
        return signal.Signals(sig).name
    except ValueError:
        return str(int(sig))


def _fail(message):
    """Print an error with the kill help page and exit non-zero."""
    from proot_distro.commands.help import HELP_COMMANDS

    msg()
    crit_error(message)
    if "kill" in HELP_COMMANDS:
        HELP_COMMANDS["kill"]()
    msg()
    sys.exit(1)


__all__ = ("command_kill",)
