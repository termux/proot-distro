"""Keep API.md in step with the public interface."""

import inspect
import re
from pathlib import Path

import proot_distro.api as api
from proot_distro.api import ProotDistro, SessionProcess

_ROOT = Path(__file__).resolve().parents[2]
_GUIDE = (_ROOT / "API.md").read_text(encoding="utf-8")
_PHASE_RE = re.compile(r'phase="([a-z-]+)"')


def _public_methods(cls):
    return [
        name for name, _ in inspect.getmembers(cls, inspect.isfunction)
        if not name.startswith("_")
    ]


def test_guide_mentions_every_public_name():
    missing = [name for name in api.__all__ if name not in _GUIDE]
    assert missing == []


def test_guide_mentions_every_facade_method():
    missing = [
        name for name in _public_methods(ProotDistro)
        if f"`{name}(" not in _GUIDE and f"`{name}`" not in _GUIDE
        and f"{name}(" not in _GUIDE
    ]
    assert missing == []


def test_guide_mentions_every_session_process_method():
    missing = [
        name for name in _public_methods(SessionProcess)
        if f"`{name}(" not in _GUIDE and f"`{name}`" not in _GUIDE
        and f"{name}(" not in _GUIDE
    ]
    assert missing == []


def test_guide_lists_every_emitted_event_phase():
    emitted = set()
    for path in (_ROOT / "proot_distro").rglob("*.py"):
        emitted.update(_PHASE_RE.findall(path.read_text(encoding="utf-8")))
    missing = sorted(phase for phase in emitted if f"`{phase}`" not in _GUIDE)
    assert missing == []


def test_guide_has_no_host_specific_values():
    for marker in ("/home/", "/Users/", "/data/data/"):
        assert marker not in _GUIDE
