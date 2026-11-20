# Exploit tests for what the TUI sample draws.
#
# samples/container_manager_tui.py shows image references and
# architectures from container metadata. A container can rewrite that
# metadata: on Termux the runtime tree is bound read-write into it. The
# status line repeats event and error text, which can quote a registry.
#
# curses does not make such text harmless. A NUL raises ValueError and
# stops the interface. A newline, carriage return or backspace moves the
# cursor, so the text lands on other rows and columns. A format character
# reaches the terminal unchanged.
#
# These tests run the sample under real curses on a pseudo-terminal,
# against containers listed through the real API. They read back what was
# drawn and collect every byte written to the terminal.

import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path

import pytest

from _builders import make_container

REPO = Path(__file__).resolve().parents[2]
SAMPLE = REPO / "samples" / "container_manager_tui.py"
HEIGHT, WIDTH = 24, 100
FOOTER = (
    "Up/Down: select   Enter/s: shell   i: install   d/x: remove",
    "r: refresh   ?: help   q: quit",
)
RLO = chr(0x202E)  # RIGHT-TO-LEFT OVERRIDE
LRI = chr(0x2066)  # LEFT-TO-RIGHT ISOLATE
WIDE = chr(0x4E2D)  # a CJK ideograph, two columns wide

# Loads the sample, lists the containers through the real API, draws once
# (or reports one event, which redraws) and saves the screen's rows.
_HARNESS = r"""
import curses
import importlib.util
import json
import sys

sample, out_path, message = sys.argv[1], sys.argv[2], json.loads(sys.argv[3])


def report(result):
    with open(out_path, "w") as handle:
        json.dump(result, handle)


try:
    curses.setupterm()
except curses.error as exc:
    report({"skip": f"no terminal description: {exc}"})
    raise SystemExit(0)

spec = importlib.util.spec_from_file_location("tui_sample", sample)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
from proot_distro.api import EventKind, OperationEvent


def draw(screen):
    manager = module.ContainerManager(screen)
    manager._reload_containers()
    if message is None:
        manager._draw()
    else:
        manager._handle_event(
            OperationEvent("install", EventKind.PROGRESS, message)
        )
    height, width = screen.getmaxyx()
    return [
        screen.instr(y, 0, width).decode("utf-8", "replace").rstrip()
        for y in range(height)
    ]


try:
    rows = curses.wrapper(draw)
except Exception as exc:
    report({"error": f"{type(exc).__name__}: {exc}"})
else:
    report({"rows": rows})
"""


def _run_tui(tmp_path, message=None):
    """Return the rows the sample drew and every byte it wrote."""
    out = tmp_path / "screen.json"
    master, slave = os.openpty()
    env = dict(os.environ, TERM="xterm", LINES=str(HEIGHT),
               COLUMNS=str(WIDTH))
    try:
        child = subprocess.Popen(
            [sys.executable, "-c", _HARNESS, str(SAMPLE), str(out),
             json.dumps(message)],
            stdin=slave, stdout=slave, stderr=slave, env=env,
            cwd=str(tmp_path),
        )
    finally:
        os.close(slave)
    written = bytearray()
    deadline = time.monotonic() + 60
    try:
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                child.kill()
                pytest.fail("the TUI sample did not finish drawing")
            if not select.select([master], [], [], remaining)[0]:
                continue
            try:
                chunk = os.read(master, 65536)
            except OSError:
                # EIO: every process holding the terminal has closed it.
                break
            if not chunk:
                break
            written += chunk
    finally:
        os.close(master)
        child.wait()
    result = json.loads(out.read_text())
    if "skip" in result:
        pytest.skip(result["skip"])
    assert "error" not in result, result["error"]
    rows = result["rows"]
    assert len(rows) == HEIGHT
    return rows, bytes(written)


def _rows_with(rows, text):
    return [index for index, row in enumerate(rows) if text in row]


def _only_row(rows, text):
    found = _rows_with(rows, text)
    assert len(found) == 1, (text, rows)
    return found[0]


def test_a_nul_in_container_metadata_does_not_stop_the_interface(tmp_path):
    # Before the fix, curses raised ValueError on the first draw and the
    # sample exited with "cannot start terminal interface".
    make_container("hostile", manifest={
        "image_ref": "evil\x00:1", "arch": "x86_64",
    })

    rows, _ = _run_tui(tmp_path)

    index = _only_row(rows, "hostile")
    assert "evil?:1" in rows[index]


def test_container_metadata_cannot_draw_over_other_rows(tmp_path):
    make_container("aa-victim", manifest={
        "image_ref": "alpine:3.21", "arch": "x86_64",
    })
    # Last in the list, so a forged row below it would not be redrawn.
    make_container("zz-hostile", manifest={
        "image_ref": "evil\rSPOOF-A\b\b\b\b\nSPOOF-B\nSPOOF-C",
        "arch": "x\x1b[2J\x9by",
    })

    rows, _ = _run_tui(tmp_path)

    victim = _only_row(rows, "aa-victim")
    hostile = _only_row(rows, "zz-hostile")
    assert rows[victim].startswith("> aa-victim ")
    assert "alpine:3.21" in rows[victim]
    assert rows[hostile].startswith("  zz-hostile ")
    for marker in ("SPOOF-A", "SPOOF-B", "SPOOF-C", "x?[2J?y"):
        assert _rows_with(rows, marker) == [hostile], marker
    # Nothing between the list and the status line.
    assert all(row == "" for row in rows[hostile + 1:HEIGHT - 3])
    assert rows[HEIGHT - 2:] == list(FOOTER)


def test_format_characters_never_reach_the_terminal(tmp_path):
    # A right-to-left override or isolate can reorder what a terminal
    # with bidirectional support shows; curses wrote them out as they were.
    make_container("hostile", manifest={
        "image_ref": f"abc{RLO}def{LRI}ghi", "arch": "x86_64",
    })

    rows, written = _run_tui(tmp_path)

    assert RLO.encode() not in written
    assert LRI.encode() not in written
    index = _only_row(rows, "hostile")
    assert "abc?def?ghi" in rows[index]


def test_event_text_stays_on_the_status_line(tmp_path):
    make_container("box", manifest={"image_ref": "alpine:3.21",
                                    "arch": "x86_64"})

    # Before the fix, the carriage return and newlines wiped the status
    # row and put the text past the end of the shorter footer row.
    rows, _ = _run_tui(tmp_path, "step\r\n\n" + " " * 40 + "INJECTED")

    assert rows[HEIGHT - 3].startswith("Status: install: step ")
    assert _rows_with(rows, "INJECTED") == [HEIGHT - 3]
    assert rows[HEIGHT - 2:] == list(FOOTER)


def test_wide_status_text_is_cut_at_the_end_of_its_row(tmp_path):
    # addnstr() limited the status to as many code points as the row has
    # columns, so wide characters ran onto the footer rows.
    make_container("box", manifest={"image_ref": "alpine:3.21",
                                    "arch": "x86_64"})

    rows, _ = _run_tui(tmp_path, WIDE * WIDTH)

    assert rows[HEIGHT - 3].startswith("Status: install: " + WIDE)
    assert rows[HEIGHT - 2:] == list(FOOTER)
