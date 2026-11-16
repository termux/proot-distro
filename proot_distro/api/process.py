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

"""Process handle returned by programmatic container sessions."""

import subprocess
from typing import Any, BinaryIO, Optional, Tuple

from proot_distro.api.errors import ProotDistroError
from proot_distro.api.models import SessionSignalResult


# How long close() waits after a kill before signalling the child directly.
_KILL_REAP_SECONDS = 5.0


class SessionProcess:
    """A running foreground login or image-entrypoint session.

    The underlying child is intentionally not exposed as part of the stable
    interface. Standard input/output/error handles follow ``subprocess.Popen``
    conventions when the caller requested pipes.
    """

    container: str
    kind: str

    def __init__(
        self, owner: Any, process: subprocess.Popen, *,
        container: str, kind: str,
    ) -> None:
        self._owner = owner
        self._process = process
        self.container = container
        self.kind = kind

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self) -> Optional[int]:
        return self._process.returncode

    @property
    def stdin(self) -> Optional[BinaryIO]:
        return self._process.stdin

    @property
    def stdout(self) -> Optional[BinaryIO]:
        return self._process.stdout

    @property
    def stderr(self) -> Optional[BinaryIO]:
        return self._process.stderr

    def poll(self) -> Optional[int]:
        """Return the exit status, or None while the session is running."""
        return self._process.poll()

    def wait(self, timeout: Optional[float] = None) -> int:
        """Wait for the session and return its exit status."""
        return self._process.wait(timeout=timeout)

    def communicate(
        self, input: Optional[bytes] = None,
        timeout: Optional[float] = None,
    ) -> Tuple[Optional[bytes], Optional[bytes]]:
        """Exchange data through requested pipes and wait for completion."""
        return self._process.communicate(input=input, timeout=timeout)

    def __enter__(self) -> "SessionProcess":
        """Return this running session for use in a ``with`` block."""
        return self

    def __exit__(
        self, _exc_type: Any, _exc_value: Any, _traceback: Any,
    ) -> bool:
        """Terminate, reap, and close pipe handles when leaving the block."""
        self.close()
        return False

    def close(self, timeout: float = 10.0) -> Optional[int]:
        """Stop a live session, escalating after *timeout*, and close pipes.

        The session is always reaped. A failure to signal the tracked tree
        is not raised here: the owned child has then been signalled
        directly, and an exception from ``close()`` would replace the one
        that is leaving a ``with`` block. Ctrl-C while waiting kills the
        session at once, reaps it and is raised again.
        """
        try:
            if self.poll() is None:
                self._quietly(self.terminate)
                try:
                    self.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self._quietly(self.kill)
                    interrupt = self._reap_after_kill()
                    if interrupt is not None:
                        raise interrupt
                except KeyboardInterrupt:
                    self._quietly(self.kill)
                    self._reap_after_kill()
                    raise
            return self.returncode
        finally:
            closed = set()
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream is not None and id(stream) not in closed:
                    closed.add(id(stream))
                    stream.close()

    @staticmethod
    def _quietly(method) -> None:
        """Signal for close(); a failed tree lookup already fell back."""
        try:
            method()
        except ProotDistroError:
            pass

    def _reap_after_kill(self) -> Optional[KeyboardInterrupt]:
        """Reap after a kill; return a Ctrl-C that arrived meanwhile.

        If the tracked-tree kill did not reach the owned child in time it
        is killed directly: SIGKILL cannot be ignored, so the wait ends.
        """
        interrupt = None
        while True:
            try:
                self._process.wait(timeout=_KILL_REAP_SECONDS)
                return interrupt
            except subprocess.TimeoutExpired:
                pass
            except KeyboardInterrupt as exc:
                interrupt = interrupt or exc
            try:
                self._process.kill()
            except OSError:
                pass

    def terminate(self) -> SessionSignalResult:
        """Request graceful teardown of the complete tracked session tree."""
        return self._signal("TERM", self._process.terminate)

    def kill(self) -> SessionSignalResult:
        """Immediately kill the complete tracked session tree."""
        return self._signal("KILL", self._process.kill)

    def _signal(self, name: str, direct) -> SessionSignalResult:
        """Signal the tracked tree, falling back to the owned child.

        A session that has already been reaped is never signalled: its PID
        may belong to an unrelated process by now. Session registration is
        best-effort and the signalling worker can fail, so the child handle
        this object owns is signalled directly when the tree lookup finds
        nothing or errors out.
        """
        if self.poll() is not None:
            return SessionSignalResult(str(self.pid), name, ())
        try:
            result = self._owner.kill_session(self.pid, signal_name=name)
        except ProotDistroError:
            if self.poll() is None:
                direct()
            raise
        if not result.pids and self.poll() is None:
            direct()
        return result


__all__ = ("SessionProcess",)
