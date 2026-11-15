#!/usr/bin/env python3
#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
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

"""Interactive terminal container manager built with the Python API.

This sample combines several public API operations in one application. It can
list installed containers, install an image, remove a selected container, and
open an interactive login shell in a selected container.

Run it from the project root::

    python samples/container_manager_tui.py

Controls::

    Up/Down or j/k   Select a container
    Enter or s       Open an interactive shell
    i                Install an image or archive
    d or x           Remove the selected container after confirmation
    r                Refresh the container list
    ?                Show help
    q                Quit

The interface uses only :mod:`curses` from Python's standard library. It is
intended for Linux and Termux terminals. A working ``proot`` executable is
required only when launching a shell.

Unless a ``RuntimeConfig`` is supplied when adapting this example, the API
uses the same containers and cache as the ``proot-distro`` command-line tool.
Installation and removal therefore change real local state. Existing
containers are never overwritten, and removal always asks for confirmation.

The application is deliberately single-threaded. API operations run one at a
time, while structured events update the status line. Before a guest shell is
started, curses restores the terminal to its normal mode. The TUI mode is
restored after the shell exits, even when startup fails or is interrupted.
"""

import curses
import os
from pathlib import Path
import sys
import unicodedata
from typing import List, Optional

# Running a repository sample directly should import the package from this
# exact checkout. An installed copy does not need the path adjustment.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from proot_distro.api import (
    API_VERSION,
    ContainerInfo,
    OperationEvent,
    ProotDistro,
    ProotDistroError,
)


# Color-pair numbers are local to this application. Terminals without color
# support simply use the fallback text attributes supplied by _style().
_HEADER_PAIR = 1
_SELECTED_PAIR = 2
_STATUS_PAIR = 3
_ERROR_PAIR = 4


def _cell_width(char: str) -> int:
    """Return how many terminal columns *char* occupies."""
    if unicodedata.combining(char):
        return 0
    return 2 if unicodedata.east_asian_width(char) in ("W", "F") else 1


def _fit(text: str, width: int) -> str:
    """Truncate or pad *text* to exactly *width* terminal columns.

    ``str.format`` counts code points, which misaligns columns for wide
    (East Asian) and combining characters.
    """
    out = []
    used = 0
    for char in text:
        step = _cell_width(char)
        if used + step > width:
            break
        out.append(char)
        used += step
    return "".join(out) + " " * (width - used)


class ContainerManager:
    """State and event loop for the interactive container manager.

    ``api`` is optional so the class can be reused with a specially configured
    ``ProotDistro`` instance. The normal sample path creates an instance whose
    event handler writes progress messages to this TUI.
    """

    def __init__(self, screen, api: Optional[ProotDistro] = None) -> None:
        """Initialize terminal state and an optional injectable API facade."""
        self.screen = screen
        self.containers: List[ContainerInfo] = []
        self.selected = 0
        self.top = 0
        self.status = "Loading containers..."
        self.status_is_error = False
        self.terminal_suspended = False

        # Configure input once. curses.wrapper() is responsible for restoring
        # the original terminal settings after run() returns.
        self.screen.keypad(True)
        self.screen.timeout(-1)
        try:
            curses.curs_set(0)
        except curses.error:
            # Some small or unusual terminals cannot change cursor visibility.
            pass
        self._initialize_colors()

        # Event handlers run synchronously in the calling thread. That makes it
        # safe to update curses without locks while install() reports progress.
        self.api = api or ProotDistro(event_handler=self._handle_event)

    def _initialize_colors(self) -> None:
        """Define optional colors while remaining usable on monochrome TTYs."""
        if not curses.has_colors():
            return
        try:
            curses.start_color()
            curses.use_default_colors()
            curses.init_pair(_HEADER_PAIR, curses.COLOR_CYAN, -1)
            curses.init_pair(_SELECTED_PAIR, curses.COLOR_BLACK, curses.COLOR_CYAN)
            curses.init_pair(_STATUS_PAIR, curses.COLOR_GREEN, -1)
            curses.init_pair(_ERROR_PAIR, curses.COLOR_RED, -1)
        except curses.error:
            # Color setup is cosmetic. Text attributes remain as fallbacks.
            pass

    @staticmethod
    def _style(pair: int, fallback: int = 0) -> int:
        """Return a color attribute when available, otherwise *fallback*."""
        if curses.has_colors():
            try:
                return curses.color_pair(pair)
            except curses.error:
                pass
        return fallback

    def _set_status(self, message: str, *, error: bool = False) -> None:
        """Store one status message for the next screen redraw."""
        self.status = message
        self.status_is_error = error

    def _handle_event(self, event: OperationEvent) -> None:
        """Display structured API events without parsing their message text."""
        context = event.phase or event.operation
        progress = ""
        if event.current is not None:
            progress = f" {event.current}"
            if event.total is not None:
                progress += f"/{event.total}"
            if event.unit:
                progress += f" {event.unit}"
        self._set_status(f"{context}{progress}: {event.message}")

        # Session startup can emit an event after endwin() has handed the
        # terminal to the guest. Never let curses overwrite that shell.
        if not self.terminal_suspended:
            try:
                self._draw()
            except curses.error:
                # A failed redraw (for instance mid-resize) must not abort
                # the API operation that reported this event.
                pass

    def _safe_addstr(
        self,
        y: int,
        x: int,
        text: str,
        attribute: int = 0,
        width: Optional[int] = None,
    ) -> None:
        """Draw bounded text and ignore terminal-edge curses errors."""
        height, screen_width = self.screen.getmaxyx()
        if y < 0 or y >= height or x < 0 or x >= screen_width:
            return
        available = screen_width - x
        if width is not None:
            available = min(available, max(0, width))
        if available <= 0:
            return
        try:
            self.screen.addnstr(y, x, text, available, attribute)
        except curses.error:
            # Writing the lower-right terminal cell may report an error even
            # when the character was displayed. A TUI should keep running.
            pass

    def _visible_rows(self) -> int:
        """Return the number of list rows available in the current terminal."""
        height, _ = self.screen.getmaxyx()
        return max(1, height - 6)

    def _keep_selection_visible(self) -> None:
        """Clamp the selection and scroll offset to the current inventory."""
        if not self.containers:
            self.selected = 0
            self.top = 0
            return
        self.selected = min(max(0, self.selected), len(self.containers) - 1)
        rows = self._visible_rows()
        if self.selected < self.top:
            self.top = self.selected
        elif self.selected >= self.top + rows:
            self.top = self.selected - rows + 1
        maximum_top = max(0, len(self.containers) - rows)
        self.top = min(max(0, self.top), maximum_top)

    def _draw(self) -> None:
        """Render the complete interface from current in-memory state."""
        self.screen.erase()
        height, width = self.screen.getmaxyx()

        # A compact warning is more useful than malformed columns on a tiny
        # terminal. Resize events cause the regular interface to be redrawn.
        if height < 10 or width < 50:
            self._safe_addstr(0, 0, "Terminal is too small (minimum 50x10).")
            self._safe_addstr(1, 0, "Resize it, or press q to quit.")
            self.screen.refresh()
            return

        header = f" PRoot-Distro API {API_VERSION} - Container Manager "
        self._safe_addstr(
            0,
            0,
            header.ljust(width),
            self._style(_HEADER_PAIR, curses.A_BOLD),
        )

        name_width = min(26, max(14, width // 4))
        arch_width = 14
        source_width = max(1, width - name_width - arch_width - 5)
        columns = (
            f"  {'NAME':<{name_width}} "
            f"{'ARCHITECTURE':<{arch_width}} SOURCE"
        )
        self._safe_addstr(2, 0, columns, curses.A_UNDERLINE)

        self._keep_selection_visible()
        rows = self._visible_rows()
        visible = self.containers[self.top:self.top + rows]
        if not visible:
            self._safe_addstr(3, 2, "No containers are installed.", curses.A_DIM)
        for offset, container in enumerate(visible):
            index = self.top + offset
            marker = ">" if index == self.selected else " "
            architecture = container.architecture or "unknown"
            source = container.image_reference or "local archive"
            row = (
                f"{marker} {_fit(container.name, name_width)} "
                f"{_fit(architecture, arch_width)} "
                f"{_fit(source, source_width)}"
            )
            attribute = 0
            if index == self.selected:
                attribute = self._style(_SELECTED_PAIR, curses.A_REVERSE)
            self._safe_addstr(3 + offset, 0, row, attribute, width)

        status_attribute = self._style(
            _ERROR_PAIR if self.status_is_error else _STATUS_PAIR,
            curses.A_BOLD if self.status_is_error else 0,
        )
        self._safe_addstr(height - 3, 0, f"Status: {self.status}", status_attribute)
        self._safe_addstr(
            height - 2,
            0,
            "Up/Down: select   Enter/s: shell   i: install   d/x: remove",
            curses.A_DIM,
        )
        self._safe_addstr(
            height - 1,
            0,
            "r: refresh   ?: help   q: quit",
            curses.A_DIM,
        )
        self.screen.refresh()

    def _reload_containers(self, preferred_name: Optional[str] = None) -> None:
        """Reload inventory and preserve a named or current selection."""
        if preferred_name is None and self.containers:
            preferred_name = self.containers[self.selected].name

        # list_containers() returns a deterministic tuple of immutable records.
        # A list is used internally because selection is index-based.
        self.containers = list(self.api.list_containers())
        if preferred_name is not None:
            for index, container in enumerate(self.containers):
                if container.name == preferred_name:
                    self.selected = index
                    break
            else:
                self.selected = min(self.selected, len(self.containers) - 1)
        self._keep_selection_visible()

    def _selected_container(self) -> Optional[ContainerInfo]:
        """Return the selected container, if the inventory is non-empty."""
        if not self.containers:
            return None
        self._keep_selection_visible()
        return self.containers[self.selected]

    def _prompt(self, label: str) -> Optional[str]:
        """Read one line from the footer, returning None when interrupted."""
        height, width = self.screen.getmaxyx()
        if height < 4 or width <= len(label) + 2:
            self._set_status("Terminal is too small for input.", error=True)
            return None

        y = height - 2
        try:
            self.screen.move(y, 0)
            self.screen.clrtoeol()
            self._safe_addstr(y, 0, label, curses.A_BOLD)
            self.screen.refresh()
            curses.echo()
            try:
                curses.curs_set(1)
            except curses.error:
                pass
            maximum = min(4096, max(1, width - len(label) - 2))
            raw = self.screen.getstr(y, len(label), maximum)
            # os.fsdecode uses the host filesystem encoding and preserves bytes
            # that are valid in a local archive path but not valid UTF-8.
            return os.fsdecode(raw)
        except (KeyboardInterrupt, curses.error):
            return None
        finally:
            try:
                curses.noecho()
            except curses.error:
                pass
            try:
                curses.curs_set(0)
            except curses.error:
                pass

    def _confirm(self, question: str) -> bool:
        """Ask a conservative yes/no question; only an explicit y confirms."""
        answer = self._prompt(f"{question} [y/N]: ")
        return answer is not None and answer.strip().lower() in ("y", "yes")

    def _install_container(self) -> None:
        """Prompt for an install source and optional local container name."""
        source = self._prompt("Image, URL, or archive path: ")
        if source is None or not source.strip():
            self._set_status("Installation cancelled.")
            return
        source = source.strip()

        name = self._prompt("Container name (blank = derive from image): ")
        if name is None:
            self._set_status("Installation cancelled.")
            return
        name = name.strip()

        self._set_status(f"Installing {source}...")
        self._draw()
        try:
            # A relative local path should begin with ./ so the API can
            # distinguish it from an OCI image reference.
            container = self.api.install(source, name=name or None)
            self._reload_containers(container.name)
            self._set_status(f"Installed {container.name}.")
        except ProotDistroError as exc:
            operation = exc.operation or "install"
            self._set_status(f"{operation} failed: {exc}", error=True)
        except KeyboardInterrupt:
            self._set_status("Installation interrupted after cleanup.", error=True)

    def _remove_container(self) -> None:
        """Confirm and permanently remove the selected container."""
        container = self._selected_container()
        if container is None:
            self._set_status("There is no container to remove.", error=True)
            return
        if not self._confirm(f"Permanently remove {container.name}?"):
            self._set_status("Removal cancelled.")
            return

        self._set_status(f"Removing {container.name}...")
        self._draw()
        try:
            self.api.remove_container(container.name)
            self._reload_containers()
            self._set_status(f"Removed {container.name}.")
        except ProotDistroError as exc:
            operation = exc.operation or "remove_container"
            self._set_status(f"{operation} failed: {exc}", error=True)
        except KeyboardInterrupt:
            self._set_status("Removal interrupted after cleanup.", error=True)

    def _restore_terminal(self) -> Optional[str]:
        """Restore curses after a child shell; return an error message if any."""
        try:
            curses.reset_prog_mode()
            curses.noecho()
            curses.cbreak()
            self.screen.keypad(True)
            try:
                curses.curs_set(0)
            except curses.error:
                pass
            self.screen.clear()
            self.screen.refresh()
            return None
        except curses.error as exc:
            return f"cannot restore the terminal interface: {exc}"

    def _launch_shell(self) -> None:
        """Suspend curses and run an interactive login shell in the selection."""
        container = self._selected_container()
        if container is None:
            self._set_status("There is no container to open.", error=True)
            return

        self._set_status(f"Opening a shell in {container.name}...")
        self._draw()
        returncode = None
        failure = None
        restore_failure = None

        try:
            # Save the TUI mode, then end curses so the guest receives a normal
            # cooked terminal with inherited stdin, stdout, and stderr.
            curses.def_prog_mode()
            curses.endwin()
            self.terminal_suspended = True
            print(f"Opening shell in {container.name}. Exit it to return to the TUI.")

            # Calling start() without a command starts the container's login
            # shell. SessionProcess ensures the tracked process tree is reaped.
            with self.api.start(container.name) as process:
                returncode = process.wait()
        except ProotDistroError as exc:
            operation = exc.operation or "start"
            failure = f"{operation} failed: {exc}"
        except KeyboardInterrupt:
            failure = "Shell interrupted after PRoot-Distro cleanup."
        except curses.error as exc:
            failure = f"cannot suspend the terminal interface: {exc}"
        finally:
            self.terminal_suspended = False
            restore_failure = self._restore_terminal()

        if restore_failure is not None:
            self._set_status(restore_failure, error=True)
        elif failure is not None:
            self._set_status(failure, error=True)
        elif returncode:
            self._set_status(
                f"Shell in {container.name} exited with status {returncode}.",
                error=True,
            )
        else:
            self._set_status(f"Shell in {container.name} closed.")

    def _show_help(self) -> None:
        """Display a modal help page and wait for one key press."""
        lines = [
            "PRoot-Distro API container manager",
            "",
            "Up/Down, j/k   Move the selection",
            "Enter, s       Open an interactive login shell",
            "i              Install an image, URL, or local archive",
            "d, x           Permanently remove the selected container",
            "r              Reload the container inventory",
            "?              Show this page",
            "q              Quit",
            "",
            "Local archive paths should start with ./, ../, or /.",
            "Press any key to return.",
        ]
        self.screen.erase()
        height, width = self.screen.getmaxyx()
        for y, line in enumerate(lines[:height]):
            attribute = curses.A_BOLD if y == 0 else 0
            self._safe_addstr(y, 1 if width > 2 else 0, line, attribute)
        self.screen.refresh()
        self.screen.getch()

    def _move_selection(self, change: int) -> None:
        """Move the selection by *change* rows and clamp it to the inventory."""
        if self.containers:
            self.selected += change
            self._keep_selection_visible()

    def run(self) -> None:
        """Load state and process keyboard commands until the user quits."""
        try:
            self._reload_containers()
            self._set_status(f"Loaded {len(self.containers)} container(s).")
        except ProotDistroError as exc:
            operation = exc.operation or "list_containers"
            self._set_status(f"{operation} failed: {exc}", error=True)

        while True:
            self._draw()
            key = self.screen.getch()
            if key in (ord("q"), ord("Q")):
                return
            if key in (curses.KEY_UP, ord("k"), ord("K")):
                self._move_selection(-1)
            elif key in (curses.KEY_DOWN, ord("j"), ord("J")):
                self._move_selection(1)
            elif key == curses.KEY_PPAGE:
                self._move_selection(-self._visible_rows())
            elif key == curses.KEY_NPAGE:
                self._move_selection(self._visible_rows())
            elif key == curses.KEY_HOME:
                self.selected = 0
                self._keep_selection_visible()
            elif key == curses.KEY_END and self.containers:
                self.selected = len(self.containers) - 1
                self._keep_selection_visible()
            elif key in (10, 13, curses.KEY_ENTER, ord("s"), ord("S")):
                self._launch_shell()
            elif key in (ord("i"), ord("I")):
                self._install_container()
            elif key in (ord("d"), ord("D"), ord("x"), ord("X")):
                self._remove_container()
            elif key in (ord("r"), ord("R")):
                try:
                    self._reload_containers()
                    self._set_status(
                        f"Loaded {len(self.containers)} container(s)."
                    )
                except ProotDistroError as exc:
                    operation = exc.operation or "list_containers"
                    self._set_status(f"{operation} failed: {exc}", error=True)
            elif key == ord("?"):
                self._show_help()
            elif key == curses.KEY_RESIZE:
                # The next loop iteration redraws from the new dimensions.
                continue


def _curses_main(screen) -> None:
    """Create and run the application inside curses.wrapper()."""
    ContainerManager(screen).run()


def main() -> int:
    """Start the TUI and convert expected failures into concise messages."""
    # curses needs a real terminal on both ends; with redirected streams it
    # fails with a confusing low-level error, so say what is wrong instead.
    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        print(
            "This interface needs an interactive terminal; standard input "
            "and output must not be redirected.",
            file=sys.stderr,
        )
        return 2
    try:
        # wrapper() initializes curses and always restores the terminal before
        # propagating an exception to this boundary.
        curses.wrapper(_curses_main)
    except ProotDistroError as exc:
        operation = exc.operation or "initialize"
        print(f"{operation} failed: {exc}", file=sys.stderr)
        return 1
    except curses.error as exc:
        print(f"terminal interface failed: {exc}", file=sys.stderr)
        return 1
    except (OSError, ValueError) as exc:
        print(f"cannot start terminal interface: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Interrupted after terminal cleanup.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
