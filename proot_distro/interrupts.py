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

"""Places where CPython could lose or misplace a Ctrl-C, held shut.

Ctrl-C reaches Python code as a KeyboardInterrupt raised by the SIGINT
handler wherever the main thread next checks for signals, and so does
Ctrl-\\, which the CLI gives SIGQUIT the same handler for. That is also
how the API cancels work: it sends its worker SIGINT and relies on the
operation's own KeyboardInterrupt handling to stop and clean up. Two
kinds of place break that:

* Between a syscall that creates something and the assignment of its
  result, where an interrupt leaves the cleanup scope nothing to clean.
* Inside Python code that C calls and whose exception C then discards.
  The signal is consumed either way, so the work goes on as if it had
  never been interrupted, and an API cancel ends in a normal result.

defer_interrupts() holds both signals back across such a place and
raises their KeyboardInterrupt on the way out, from code that
propagates it.

A third kind of place is a cleanup, where an interrupt is not lost but
lands too late: finish_cleanup() is for those.
"""

import contextlib
import signal


# The signals that arrive as a KeyboardInterrupt: SIGINT for Ctrl-C and
# an API cancel, and SIGQUIT for Ctrl-\ in the CLI (cli.main).
_INTERRUPT_SIGNALS = (signal.SIGINT, signal.SIGQUIT)


@contextlib.contextmanager
def defer_interrupts():
    """Hold a Ctrl-C or a Ctrl-\\ back for the body; raise it on the way out.

    For the body, each of SIGINT and SIGQUIT whose handler is Python's
    own default_int_handler gets one that only notes that the signal
    came, and the KeyboardInterrupt is raised here once the body is
    over. Swapping the handler is what holds in a process with more than
    one thread. Blocking the signal did not: a mask is a thread's own,
    and the kernel hands a signal sent to the process, as a Ctrl-C and an
    API cancel are, to any thread that does not block it. Python then
    runs the handler in the main thread wherever that next checks, which
    can be inside the very place the body guards.

    Any other Python handler is the program's own, and what it does is
    not this module's to replay, so its signal is blocked in the calling
    thread instead, which holds it while no other thread takes it. A
    signal with no Python handler, SIG_DFL or SIG_IGN, raises nothing and
    is left alone. Only the main thread of the main interpreter may set a
    handler, and that is also the only thread Python runs one in, so in
    any other there is nothing to lose.

    The two default handlers go back one after the other, and the signal
    whose handler went back first can cut that short. A noting handler
    left installed that way acts, from then on, as the default handler it
    stood in for, so the key it was left on is not silenced. The body
    must not change either handler itself.
    """
    caught = []
    over = []

    def note(signum, _frame):
        if over:
            signal.signal(signum, signal.default_int_handler)
            raise KeyboardInterrupt
        caught.append(signum)

    pthread_sigmask = getattr(signal, "pthread_sigmask", None)
    previous_mask = None
    try:
        held = set()
        for signum in _INTERRUPT_SIGNALS:
            handler = signal.getsignal(signum)
            if handler is signal.default_int_handler:
                try:
                    signal.signal(signum, note)
                    continue
                except ValueError:
                    # Not the main thread of the main interpreter.
                    pass
            if callable(handler):
                held.add(signum)
        if held and pthread_sigmask is not None:
            previous_mask = pthread_sigmask(signal.SIG_BLOCK, held)
        yield
    finally:
        # From here on a noting handler raises: one still installed after
        # this has nobody left to report what it noted.
        over.append(True)
        try:
            if previous_mask is not None:
                pthread_sigmask(signal.SIG_SETMASK, previous_mask)
        finally:
            for signum in _INTERRUPT_SIGNALS:
                if signal.getsignal(signum) is note:
                    signal.signal(signum, signal.default_int_handler)
        if caught:
            raise KeyboardInterrupt


def open_tar_member(tf, member):
    """Return tf.extractfile(member) without the window that drops Ctrl-C.

    extractfile() wraps tarfile's raw member stream in an
    io.BufferedReader, and BufferedReader's constructor asks that stream
    for tell() and clears whatever it raises (_buffered_init in CPython's
    Modules/_io/bufferedio.c). tarfile's stream is Python code, so a
    signal handled there raised its KeyboardInterrupt into C that threw
    it away. Extraction calls this once per member, and about one
    interrupt in a hundred landing mid-extraction was lost that way: a
    Ctrl-C did nothing, an API cancel returned a finished install.

    The caller closes the result itself, in a finally, rather than
    leaving it to the finalizer, which reports and drops exceptions the
    same way. None comes back for a member with no data.
    """
    with defer_interrupts():
        return tf.extractfile(member)


def finish_cleanup(cleanup, *, interrupted):
    """Run *cleanup* to its end through one interrupt; a second stops it.

    A cleanup comes after the work, so it is where a late Ctrl-C, an API
    cancel, or the stop that follows a failing event handler tends to
    land, and removing a large tree takes long enough for one to land
    there. One landing there cut the removal short and stranded what it
    was removing. So the first interrupt lets *cleanup* run again, to its
    end, and is raised after it; a second stops it where it stands, as a
    second Ctrl-C stops the wait for a build's RUN step. *cleanup* must
    therefore be safe to run twice.

    *interrupted* says the caller is already unwinding from an interrupt
    it caught, which makes one arriving here the second.
    """
    if interrupted:
        cleanup()
        return
    try:
        cleanup()
    except KeyboardInterrupt:
        cleanup()
        raise


__all__ = ("defer_interrupts", "finish_cleanup", "open_tar_member")
