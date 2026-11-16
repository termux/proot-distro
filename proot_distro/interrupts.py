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
handler wherever the main thread next checks for signals. That is also
how the API cancels work: it sends its worker SIGINT and relies on the
operation's own KeyboardInterrupt handling to stop and clean up. Two
kinds of place break that:

* Between a syscall that creates something and the assignment of its
  result, where an interrupt leaves the cleanup scope nothing to clean.
* Inside Python code that C calls and whose exception C then discards.
  The SIGINT is consumed either way, so the work goes on as if it had
  never been interrupted, and an API cancel ends in a normal result.

defer_sigint() keeps SIGINT pending across such a place and raises it
on the way out, from code that propagates it.
"""

import contextlib
import signal


@contextlib.contextmanager
def defer_sigint():
    """Hold SIGINT pending for the body; raise it on the way out.

    The signal is blocked in the calling thread, so the kernel keeps it
    rather than running the handler. Restoring the mask delivers it, and
    signal.pthread_sigmask() runs the handlers before it returns, so the
    KeyboardInterrupt comes from here. Where pthread_sigmask() does not
    exist the body runs unprotected.
    """
    pthread_sigmask = getattr(signal, "pthread_sigmask", None)
    if pthread_sigmask is None:
        yield
        return
    previous = pthread_sigmask(signal.SIG_BLOCK, {signal.SIGINT})
    try:
        yield
    finally:
        pthread_sigmask(signal.SIG_SETMASK, previous)


def open_tar_member(tf, member):
    """Return tf.extractfile(member) without the window that drops Ctrl-C.

    extractfile() wraps tarfile's raw member stream in an
    io.BufferedReader, and BufferedReader's constructor asks that stream
    for tell() and clears whatever it raises (_buffered_init in CPython's
    Modules/_io/bufferedio.c). tarfile's stream is Python code, so a
    SIGINT handled there raised its KeyboardInterrupt into C that threw
    it away. Extraction calls this once per member, and about one
    interrupt in a hundred landing mid-extraction was lost that way: a
    Ctrl-C did nothing, an API cancel returned a finished install.

    The caller closes the result itself, in a finally, rather than
    leaving it to the finalizer, which reports and drops exceptions the
    same way. None comes back for a member with no data.
    """
    with defer_sigint():
        return tf.extractfile(member)


__all__ = ("defer_sigint", "open_tar_member")
