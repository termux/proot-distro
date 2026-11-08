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


class SessionProcess:
    """A running foreground login or image-entrypoint session.

    The underlying child is intentionally not exposed as part of the stable
    interface. Standard input/output/error handles follow ``subprocess.Popen``
    conventions when the caller requested pipes.
    """

    def __init__(self, owner, process, *, container: str, kind: str) -> None:
        self._owner = owner
        self._process = process
        self.container = container
        self.kind = kind

    @property
    def pid(self) -> int:
        return self._process.pid

    @property
    def returncode(self):
        return self._process.returncode

    @property
    def stdin(self):
        return self._process.stdin

    @property
    def stdout(self):
        return self._process.stdout

    @property
    def stderr(self):
        return self._process.stderr

    def poll(self):
        """Return the exit status, or None while the session is running."""
        return self._process.poll()

    def wait(self, timeout=None):
        """Wait for the session and return its exit status."""
        return self._process.wait(timeout=timeout)

    def communicate(self, input=None, timeout=None):
        """Exchange data through requested pipes and wait for completion."""
        return self._process.communicate(input=input, timeout=timeout)

    def __enter__(self):
        """Return this running session for use in a ``with`` block."""
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback):
        """Terminate, reap, and close pipe handles when leaving the block."""
        self.close()
        return False

    def close(self, timeout=10.0):
        """Stop a live session, escalating after *timeout*, and close pipes."""
        try:
            if self.poll() is None:
                self.terminate()
                try:
                    self.wait(timeout=timeout)
                except subprocess.TimeoutExpired:
                    self.kill()
                    self.wait()
            return self.returncode
        finally:
            closed = set()
            for stream in (self.stdin, self.stdout, self.stderr):
                if stream is not None and id(stream) not in closed:
                    closed.add(id(stream))
                    stream.close()

    def terminate(self):
        """Request graceful teardown of the complete tracked session tree."""
        result = self._owner.kill_session(self.pid, signal_name="TERM")
        if not result.pids and self.poll() is None:
            # Session registration is best-effort. This PID is still safe to
            # signal directly because it is the child handle this object owns.
            self._process.terminate()
        return result

    def kill(self):
        """Immediately kill the complete tracked session tree."""
        result = self._owner.kill_session(self.pid, signal_name="KILL")
        if not result.pids and self.poll() is None:
            self._process.kill()
        return result


__all__ = ("SessionProcess",)
