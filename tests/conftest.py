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

import os
import shutil
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
from proot_distro import constants, locking, message  # noqa: E402

import _builders  # noqa: E402


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


def pytest_sessionfinish(session, exitstatus):
    shutil.rmtree(_SANDBOX, ignore_errors=True)


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

@pytest.fixture(autouse=True)
def _clean_runtime():
    """Reset process globals and wipe the runtime/cache trees around each test."""
    message.set_quiet(False)
    locking._held_exclusive.clear()
    for d in (constants.RUNTIME_DIR, constants.BASE_CACHE_DIR):
        shutil.rmtree(d, ignore_errors=True)
    os.makedirs(constants.CONTAINERS_DIR, exist_ok=True)
    os.makedirs(constants.BASE_CACHE_DIR, exist_ok=True)
    os.makedirs(constants.LAYER_CACHE_DIR, exist_ok=True)
    os.makedirs(constants.MANIFEST_CACHE_DIR, exist_ok=True)
    yield
    message.set_quiet(False)
    locking._held_exclusive.clear()


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
