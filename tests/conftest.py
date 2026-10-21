# Shared pytest configuration for the proot-distro test suite.
#
# CRITICAL ORDERING: proot_distro.constants computes RUNTIME_DIR /
# CONTAINERS_DIR / LAYER_CACHE_DIR / MANIFEST_CACHE_DIR / DEFAULT_PATH_ENV /
# IS_TERMUX ONCE at import time, and most modules bind those values by
# `from ...constants import X`. We therefore must point XDG_*/HOME at a
# throwaway sandbox and neutralise the Termux/auth environment *before* any
# proot_distro module is imported anywhere in the process. pytest imports the
# root conftest.py before collecting test modules, so doing it here at module
# top is sufficient for the whole session.

import ctypes
import os
import sys
import tempfile

# ---------------------------------------------------------------------------
# Sandbox the environment BEFORE importing proot_distro.
# ---------------------------------------------------------------------------

_SANDBOX = tempfile.mkdtemp(prefix="pd-tests-")
os.environ["XDG_DATA_HOME"] = os.path.join(_SANDBOX, "data")
os.environ["XDG_CACHE_HOME"] = os.path.join(_SANDBOX, "cache")
os.environ["HOME"] = os.path.join(_SANDBOX, "home")
os.makedirs(os.environ["HOME"], exist_ok=True)

# Deterministic, color-free output so assertions can match plain text.
os.environ["PD_FORCE_NO_COLORS"] = "1"

# Force non-Termux detection and anonymous registry auth regardless of the
# host running the suite.
for _v in (
    "TERMUX_VERSION",
    "TERMUX_APP__APP_VERSION_NAME",
    "TERMUX_APP__PACKAGE_NAME",
    "TERMUX__HOME",
    "TERMUX__PREFIX",
    "PD_DOCKER_AUTH",
    "PROOT_NO_SECCOMP",
    "PROOT_VERBOSE",
):
    os.environ.pop(_v, None)

# Make tests/_builders.py importable as a top-level module from any test
# (pytest's default "prepend" import mode only puts each test's own dir on
# sys.path, not the tests/ root).
sys.path.insert(0, os.path.dirname(__file__))

import pytest  # noqa: E402

# Safe now: constants resolves into the sandbox on first import.
from proot_distro import constants, dirfd, locking, message  # noqa: E402
from proot_distro import prootcheck  # noqa: E402

import _builders  # noqa: E402

# The real self-test, for the tests that exercise it (`real_proot_probe`).
_REAL_PROOT_PROBE = prootcheck._probe


# ---------------------------------------------------------------------------
# Guard rails: never let the suite touch a real proot-distro installation.
# ---------------------------------------------------------------------------

def _assert_sandboxed():
    for path in (constants.RUNTIME_DIR, constants.BASE_CACHE_DIR):
        if not os.path.abspath(path).startswith(_SANDBOX):
            raise RuntimeError(
                f"proot-distro path {path!r} is not inside the test sandbox "
                f"{_SANDBOX!r}; refusing to run (would risk real data)."
            )
    if constants.IS_TERMUX:
        raise RuntimeError("IS_TERMUX must be False in the test environment.")


_assert_sandboxed()


def _remove_tree(path):
    """Remove *path* and everything under it; the paths that would not go.

    Not shutil.rmtree: a deep tree is exactly what much of this suite
    builds, and rmtree recurses. RecursionError is not an OSError, so
    ignore_errors=True never caught it -- one tree a test left behind
    turned every later test's setup into an error and the end of the
    session into a traceback that took the report with it, burying the
    failure that left the tree there. dirfd.remove_tree walks on an
    explicit stack with a bounded number of descriptors, and relaxes a
    directory a test sealed on its way in.
    """
    left = []
    dirfd.remove_tree(
        path, on_error=lambda rel, exc: left.append(
            f"{os.path.join(path, rel) if rel else path}: {exc}"))
    return left


def pytest_sessionfinish(session, exitstatus):
    # Best effort: an exception here would replace the session's report.
    _remove_tree(_SANDBOX)


def pytest_collection_modifyitems(config, items):
    """Skip tests marked `live` unless RUN_LIVE_TESTS=1 is set."""
    if os.environ.get("RUN_LIVE_TESTS"):
        return
    skip_live = pytest.mark.skip(
        reason="live test (network/proot); set RUN_LIVE_TESTS=1 to run"
    )
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


# ---------------------------------------------------------------------------
# Per-test isolation.
# ---------------------------------------------------------------------------

def _clear_child_subreaper():
    """Drop the subreaper flag a RUN step sets on the process it runs in.

    run_step asks for PR_SET_CHILD_SUBREAPER so a step's daemonised
    leftovers are reparented onto the build process rather than onto
    init. It is process-wide and survives the test that triggered it,
    which changes where *every* orphan in this process lands afterwards
    -- `kill`'s process-tree walk is one of the things that notices.
    """
    from proot_distro.helpers.build_engine import run_step

    run_step._subreaper_asked = None
    try:
        ctypes.CDLL(None, use_errno=True).prctl(
            run_step._PR_SET_CHILD_SUBREAPER, 0, 0, 0, 0
        )
    except (OSError, AttributeError, ValueError):
        pass


@pytest.fixture(autouse=True)
def _clean_runtime():
    """Reset process globals and wipe the runtime/cache trees around each test."""
    message.set_quiet(False)
    locking._held_exclusive.clear()
    _clear_child_subreaper()
    for d in (constants.RUNTIME_DIR, constants.BASE_CACHE_DIR):
        left = _remove_tree(d)
        if left:
            # The test is about to run on whatever an earlier one left
            # behind; say so here rather than let it fail for no reason
            # of its own.
            raise RuntimeError(
                "cannot reset the sandbox before this test:\n  "
                + "\n  ".join(left))
    os.makedirs(constants.CONTAINERS_DIR, exist_ok=True)
    os.makedirs(constants.BASE_CACHE_DIR, exist_ok=True)
    os.makedirs(constants.LAYER_CACHE_DIR, exist_ok=True)
    os.makedirs(constants.MANIFEST_CACHE_DIR, exist_ok=True)
    yield
    message.set_quiet(False)
    locking._held_exclusive.clear()
    _clear_child_subreaper()


@pytest.fixture(autouse=True)
def _proot_confines(monkeypatch):
    """Answer proot's self-test with "confines" unless a test asks otherwise.

    login and RUN steps probe the proot binary before using it. Most
    tests stand in a fake path for proot and a fake Popen or execvpe for
    the exec, so a real probe would launch whatever that path names, or
    land in the test's fake. A test that wants the real probe requests
    `real_proot_probe`; one that wants a verdict patches `_probe` itself.
    """
    prootcheck._verdicts.clear()
    monkeypatch.setattr(prootcheck, "_probe", lambda proot_bin, toggles: True)
    yield
    prootcheck._verdicts.clear()


@pytest.fixture
def real_proot_probe(monkeypatch):
    """Undo the autouse stub above: run the real proot self-test."""
    monkeypatch.setattr(prootcheck, "_probe", _REAL_PROOT_PROBE)


# ---------------------------------------------------------------------------
# Convenience fixtures.
# ---------------------------------------------------------------------------

@pytest.fixture
def builders():
    """Expose the archive/rootfs/ELF builder helpers as a module."""
    return _builders


@pytest.fixture
def paths():
    """Expose the live (sandboxed) proot-distro path constants."""
    return constants


@pytest.fixture
def sandbox_tmp(tmp_path):
    """A scratch directory outside the proot-distro runtime tree."""
    return tmp_path
