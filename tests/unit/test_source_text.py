# The repository's own text holds no invisible or reordering characters.
#
# A control or format character in source -- a right-to-left override, a
# zero-width space, a BOM -- makes the code read differently from what it
# does ("Trojan Source"), and an editor or a review page shows nothing.
# Where code or a test needs one, it spells it as an escape ("\u202e").
# The escape tests once held them raw, written by a tool that decoded the
# escapes, and the Dockerfile parser's BOM check held one too.

import subprocess
import unicodedata
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
_SUFFIXES = {".py", ".md", ".toml", ".cfg", ".txt", ".json", ".yml", ".yaml",
             ".sh", ".in"}
# Tab, newline and carriage return are text; nothing else in these
# categories is.
_ALLOWED = {"\t", "\n", "\r"}
_REFUSED = {"Cc", "Cf", "Cs", "Zl", "Zp"}


def _tracked_text_files():
    try:
        listed = subprocess.run(
            ["git", "ls-files", "-z"], cwd=_ROOT, capture_output=True,
            check=True,
        ).stdout
    except (OSError, subprocess.CalledProcessError):
        pytest.skip("not a git checkout")
    for name in listed.decode("utf-8", "surrogateescape").split("\0"):
        path = _ROOT / name
        if name and path.suffix in _SUFFIXES and path.is_file():
            yield name, path


def test_no_tracked_text_holds_a_raw_control_or_format_character():
    found = []
    for name, path in _tracked_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        for number, line in enumerate(text.split("\n"), 1):
            for char in line:
                if (char not in _ALLOWED
                        and unicodedata.category(char) in _REFUSED):
                    found.append(f"{name}:{number}: U+{ord(char):04X}")
    assert not found, found
