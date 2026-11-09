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

# Architecture: Shared teardown of a container session's entire process tree,
# anchored to the session registry (session.py) that inventories read.
#
# Two properties of proot shape everything here, both read off its
# sources (proot/src/tracee/event.c):
#
#   1. proot's event loop installs SIG_IGN for *every* signal except
#      SIGQUIT / SIGILL / SIGABRT / SIGFPE / SIGSEGV (which run
#      kill_all_tracees2), SIGUSR1 / SIGUSR2 (talloc dumps) and the job
#      control set (left at their default). A plain SIGTERM aimed at the
#      container root is therefore a silent no-op, and so is SIGINT or
#      SIGHUP. SIGQUIT is the one graceful lever: its handler SIGKILLs
#      every tracee, then lets the event loop exit through ECHILD.
#      _escalate() is built on exactly that.
#
#   2. proot sets no PTRACE_O_EXITKILL, and its `--kill-on-exit` cleanup
#      only runs when the event loop observes a *graceful* exit (and is
#      never passed at all off Termux). So `kill -9 <proot>` leaves the
#      whole guest running, reparented to init.
#
# Because of (2) a session cannot be identified by its recorded root PID
# alone. The strong identifier is the session registry file: its
# descriptor is inherited across execvpe() by proot and every guest
# process, so scanning /proc for that inode (session_holders()) yields
# the live members no matter what happened to the root — and cannot be
# fooled by PID reuse. Descendants that closed the inherited fd are
# picked up by the /proc tree walk rooted at the topmost holders. The
# comm-based check survives only as a fallback for hosts where the fd
# scan comes up empty.
#
# Targets are always resolved against active_sessions(), so `kill` can
# only ever signal tracked proot sessions, never an arbitrary host PID.

import os
import signal
import time

from proot_distro.constants import PROGRAM_NAME
from proot_distro.names import NAME_RULE_HINT, is_valid_name
from proot_distro.operations.context import OperationContext
from proot_distro.operations.errors import (
    SessionSignalFailure,
    SessionSignalValidationFailure,
)
from proot_distro.operations.models import SessionSignal
from proot_distro.session import (
    active_sessions,
    session_holders,
    session_is_live,
)

# Signals that mean something other than "terminate": job control, and
# the two proot reserves for its talloc dumps. A request for one of
# these is delivered exactly as asked and never escalated -- turning
# `kill -s STOP` into a teardown would be a nasty surprise.
_NON_TERMINATING_SIGNALS = frozenset(
    getattr(signal, name) for name in (
        "SIGSTOP", "SIGCONT", "SIGTSTP", "SIGTTIN", "SIGTTOU",
        "SIGCHLD", "SIGURG", "SIGWINCH", "SIGUSR1", "SIGUSR2",
    ) if hasattr(signal, name)
)

# How long the requested signal is given to drain a session before the
# container root is torn down with SIGQUIT, and again before the final
# SIGKILL sweep. Only ever waited out when the guest ignored the signal,
# so a cooperative guest still exits immediately.
_GRACE_SECONDS = 2.0
_POLL_INTERVAL = 0.05


def signal_sessions(
    target=None, *, all_sessions=False, signal_name=None, context=None,
) -> SessionSignal:
    """Signal tracked sessions and, for terminating signals, drain them."""
    context = context if context is not None else OperationContext()
    if not isinstance(all_sessions, bool):
        raise SessionSignalValidationFailure(
            "all_sessions must be a boolean."
        )
    sig = _parse_signal(signal_name) if signal_name else signal.SIGTERM
    label = _signal_label(sig)
    kill_all = all_sessions
    if kill_all and target is not None:
        raise SessionSignalValidationFailure(
            "a target cannot be combined with --all."
        )
    if not kill_all and target is None:
        raise SessionSignalValidationFailure(
            "specify a PID, a container name, or --all."
        )
    if isinstance(target, bool):
        raise SessionSignalValidationFailure(
            "target must be a PID or container name."
        )
    if isinstance(target, int):
        if target <= 0:
            raise SessionSignalValidationFailure(
                "target PID must be positive."
            )
        target = str(target)
    elif target is not None and (not isinstance(target, str) or not target):
        raise SessionSignalValidationFailure(
            "target must be a PID or container name."
        )

    result_target = "*" if kill_all else target

    sessions = active_sessions()
    selected = _select_sessions(sessions, target, kill_all)
    selected_pids = tuple(
        session["pid"] for session in selected
        if isinstance(session.get("pid"), int)
        and not isinstance(session.get("pid"), bool)
        and session["pid"] > 0
    )
    if not selected:
        context.info(
            _no_match_message(target, kill_all),
            phase="signal-session", resource=result_target,
        )
        return SessionSignal(result_target, label, (), 0, 0, ())

    tracked = _tracked_sessions(selected, context=context)
    if not tracked:
        context.info(
            "No live session processes to signal.",
            phase="signal-session", resource=result_target,
        )
        return SessionSignal(
            result_target, label, selected_pids, 0, 0, ()
        )

    delivered = _teardown(tracked, sig, context=context)
    survivors = (
        () if sig in _NON_TERMINATING_SIGNALS
        else tuple(_survivors(tracked))
    )
    result = SessionSignal(
        result_target, label, selected_pids, len(tracked), delivered, survivors,
    )
    if survivors:
        raise SessionSignalFailure(result)
    if sig in _NON_TERMINATING_SIGNALS:
        context.info(
            f"Sent {label} to {delivered} process(es) across "
            f"{len(tracked)} session(s).",
            phase="signal-session", resource=result_target,
        )
    else:
        context.info(
            f"Terminated {len(tracked)} session(s) with {label} "
            f"({delivered} signal(s) delivered).",
            phase="signal-session", resource=result_target,
        )
    return result


def _select_sessions(sessions, target, kill_all):
    """Resolve the requested target to a list of session records."""
    if kill_all:
        return list(sessions)
    if _is_pid_token(target):
        # A bare number is always interpreted as a PID (as shown by `ps`),
        # even though container names may also be all-digits.
        pid = int(target)
        return [s for s in sessions if s.get("pid") == pid]
    if not isinstance(target, str) or not is_valid_name(target):
        raise SessionSignalValidationFailure(
            f"container name '{target}' is not valid. {NAME_RULE_HINT}",
            show_help=False,
        )
    return [s for s in sessions if s.get("container") == target]


def _is_pid_token(target):
    """True when *target* is a plain decimal PID.

    str.isdigit() on its own also accepts non-ASCII digit characters
    (superscripts, circled digits) that int() then rejects; the ASCII
    guard is what turns those into an ordinary "invalid name" report
    instead of an unhandled ValueError.
    """
    return target.isascii() and target.isdigit()


def _no_match_message(target, kill_all):
    """Friendly explanation when nothing matched the requested target."""
    if kill_all:
        return "No active sessions."
    if _is_pid_token(target):
        return f"No active session with PID {int(target)}."
    return f"No active sessions for container '{target}'."


def _tracked_sessions(selected, context=None):
    """Keep the sessions that still have at least one live process.

    A session whose registry file nobody holds and whose recorded PID is
    gone has simply exited; one whose PID now belongs to an unrelated
    process is reported, never signalled.
    """
    context = context if context is not None else OperationContext()
    pid_ppid = _read_pid_ppid()
    tracked = []
    for sess in selected:
        pid = sess.get("pid")
        if (isinstance(pid, bool) or not isinstance(pid, int) or pid <= 0):
            continue
        if _session_roots(pid, pid_ppid):
            tracked.append(sess)
        elif _root_is_proot(pid) is False:
            context.warning(
                f"PID {pid} is no longer a {PROGRAM_NAME} session; "
                f"skipping.",
                phase="signal-session", resource=str(pid),
            )
    return tracked


def _session_roots(pid, pid_ppid):
    """Live entry points of the session registered under *pid*.

    Prefers the processes holding the session registry file open, as
    that set stays correct after the root proot was SIGKILLed and its
    guests were reparented to init. Only the topmost holders are
    returned; their descendants come from the tree walk, which also
    catches guests that closed the inherited descriptor.

    An empty holder set means either that /proc could not be read (an
    Android kernel with hidepid, say) or that the session ended between
    the registry listing and this scan. Only the first is a reason to
    fall back to signalling the recorded PID directly: in the second the
    kernel is free to have handed that number to an unrelated process,
    and `_root_is_proot` says yes to any proot, including one this
    program never started. The registry file settles which it is,
    because its exclusive flock is held for exactly as long as some
    member of the session is running.
    """
    holders = {p for p in session_holders(pid) if _is_alive(p)}
    if holders:
        return _forest_roots(holders, pid_ppid)
    if not session_is_live(pid):
        return []
    if _is_alive(pid) and _root_is_proot(pid) is True:
        return [pid]
    return []


def _forest_roots(pids, pid_ppid):
    """Members of *pids* whose parent is not itself in *pids*.

    A parent missing from the map has already exited, which makes its
    child a root too.
    """
    return sorted(p for p in pids if pid_ppid.get(p) not in pids)


def _live_roots(tracked, pid_ppid=None):
    """Re-derive the live entry points of every tracked session.

    Recomputed from scratch on each call rather than cached: once a root
    dies its guests reparent to init and are unreachable from the old
    root, but they still hold the registry file, so the holder scan
    keeps finding them.
    """
    if pid_ppid is None:
        pid_ppid = _read_pid_ppid()
    roots = set()
    for sess in tracked:
        roots.update(_session_roots(sess.get("pid"), pid_ppid))
    return sorted(roots)


def _teardown(tracked, sig, context=None):
    """Deliver *sig*, then make sure the sessions are actually gone.

    Returns the number of signals delivered. The escalation steps are
    reached only when the guest outlived the signal it was asked to
    honour, so a cooperative teardown costs no extra wait.
    """
    context = context if context is not None else OperationContext()
    delivered = _signal_pass(_live_roots(tracked), sig, context=context)

    if sig in _NON_TERMINATING_SIGNALS:
        return delivered

    if _wait_for_drain(tracked):
        return delivered

    # Still up. proot ignores SIGTERM/SIGINT/SIGHUP outright, so a
    # graceful request never reached the container root; SIGQUIT is the
    # signal its event loop acts on, and its handler SIGKILLs every
    # tracee before the loop leaves through ECHILD.
    delivered += _escalate(_live_roots(tracked), context=context)
    if _wait_for_drain(tracked):
        return delivered

    # Whatever is left either escaped tracing or was orphaned by an
    # earlier `kill -9` on proot, so nothing will reap it but us.
    return delivered + _signal_pass(
        _live_roots(tracked), signal.SIGKILL, context=context
    )


def _escalate(roots, context=None):
    """SIGQUIT the container roots that are still up.

    Roots that are not proot are guests orphaned by an earlier
    `kill -9`; SIGQUIT would only make them dump core, so they are left
    to the SIGKILL sweep that follows.
    """
    context = context if context is not None else OperationContext()
    delivered = 0
    for pid in roots:
        if not _is_alive(pid) or _root_is_proot(pid) is not True:
            continue
        try:
            os.kill(pid, signal.SIGQUIT)
            delivered += 1
        except ProcessLookupError:
            pass
        except PermissionError:
            context.warning(
                f"no permission to signal PID {pid}.",
                phase="signal-session", resource=str(pid),
            )
    return delivered


def _drained(tracked, pid_ppid=None):
    """True when no tracked session has a live process left.

    The flock probe `ps` uses is the cheap first cut and short-circuits
    the common case, where the session is still holding on. Only once it
    reports every lock released is the answer confirmed against /proc,
    so a guest that closed the inherited descriptor is not mistaken for
    a finished session -- and so this stays consistent with the tree
    walk _survivors() reports from.
    """
    for sess in tracked:
        if session_is_live(sess.get("pid")):
            return False
    return not _live_roots(tracked, pid_ppid)


def _wait_for_drain(tracked):
    """Poll until the tracked sessions are gone or the grace runs out.

    Returns True if everything drained within the grace period. The wait
    is only ever paid when the guest ignored the signal it was sent.
    """
    deadline = time.monotonic() + _GRACE_SECONDS
    while True:
        if _drained(tracked):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(_POLL_INTERVAL)


def _survivors(tracked):
    """Processes of *tracked* still running after the teardown."""
    pid_ppid = _read_pid_ppid()
    alive = set()
    for root in _live_roots(tracked, pid_ppid):
        alive |= {p for p in _collect_tree(root, pid_ppid) if _is_alive(p)}
    return sorted(alive)


def _signal_pass(roots, sig, context=None):
    """Read /proc, collect every root's tree, and signal the union once."""
    context = context if context is not None else OperationContext()
    pid_ppid = _read_pid_ppid()
    tree = set()
    for root in roots:
        tree |= _collect_tree(root, pid_ppid)

    self_pid = os.getpid()
    delivered = 0
    for pid in sorted(tree):
        if pid in (0, 1) or pid == self_pid:
            continue
        if not _is_alive(pid):
            continue  # exited already, or a zombie awaiting its reaper
        try:
            os.kill(pid, sig)
            delivered += 1
        except ProcessLookupError:
            pass  # exited between enumeration and the signal
        except PermissionError:
            context.warning(
                f"no permission to signal PID {pid}.",
                phase="signal-session", resource=str(pid),
            )
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


def _is_alive(pid):
    """True when *pid* exists and is not a zombie.

    os.kill(pid, 0) would call a zombie alive, which makes a torn-down
    proot look like a survivor for as long as its parent takes to reap
    it. The comm field may contain parentheses, hence the rsplit.
    """
    try:
        with open(f"/proc/{pid}/stat") as fh:
            data = fh.read()
    except OSError:
        return False
    try:
        return data.rsplit(")", 1)[1].split()[0] != "Z"
    except IndexError:
        return False


def _proot_comm_names():
    """comm values a container root may legitimately have.

    /proc/<pid>/comm is the basename of the executed binary truncated to
    15 characters, and PD_PROOT_BIN lets that binary be named anything,
    so pinning the check to the literal "proot" would refuse to stop
    perfectly valid sessions.
    """
    names = {"proot"}
    override = os.environ.get("PD_PROOT_BIN")
    if override:
        names.add(os.path.basename(override))
    return {name[:15] for name in names}


def _root_is_proot(pid):
    """Return True if /proc/<pid>/comm names proot, False on a mismatch,
    None when the entry is unreadable (the process is already gone)."""
    try:
        with open(f"/proc/{pid}/comm") as fh:
            return fh.read().strip() in _proot_comm_names()
    except OSError:
        return None


def _parse_signal(name):
    """Resolve a user-supplied signal ('TERM', 'SIGTERM', '9') to a number.

    Raises a typed validation failure on an unknown name or number.
    """
    if not isinstance(name, str):
        raise SessionSignalValidationFailure(
            "signal name must be a string."
        )
    raw = (name or "").strip().upper()
    if not raw:
        raise SessionSignalValidationFailure("signal name cannot be empty.")
    if raw.isdigit():
        try:
            return signal.Signals(int(raw))
        except ValueError:
            raise SessionSignalValidationFailure(
                f"invalid signal number '{name}'."
            ) from None
    if not raw.startswith("SIG"):
        raw = "SIG" + raw
    try:
        return signal.Signals[raw]
    except KeyError:
        raise SessionSignalValidationFailure(
            f"invalid signal name '{name}'."
        ) from None


def _signal_label(sig):
    """Human-readable signal name for reporting (e.g. 'SIGKILL')."""
    try:
        return signal.Signals(sig).name
    except ValueError:
        return str(int(sig))


__all__ = ("signal_sessions",)
