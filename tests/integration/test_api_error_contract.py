"""The typed errors each API method can raise, proven against real workers.

Docstrings and API.md both promise a set of specific exceptions per method.
Those promises used to be written from a partial reading of the code, and
were incomplete (BusyError was missing from most methods). This module is
the single source of truth for them:

* every (method, error) pair in ``VERIFIED`` is provoked for real, through a
  real worker process, and must raise exactly that class;
* ``STATIC_ONLY`` lists the few pairs that share a code path with a verified
  one but cannot be provoked offline, each with the reason;
* the docstring ``Raises:`` section and the API.md table of every method
  must equal ``VERIFIED + STATIC_ONLY`` for it, so neither can drift.

``InvalidArgumentError``, ``OperationCancelledError``, ``OperationError`` and
``SessionStartupTimeoutError`` are generic and documented for every method
that can produce them; they are not part of the per-method comparison.
"""

import http.server
import inspect
import os
import re
import shutil
import subprocess
import sys
import tarfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from proot_distro.api import (
    AlreadyExistsError,
    AuthenticationError,
    BusyError,
    IntegrityError,
    NetworkError,
    NotFoundError,
    PartialOperationError,
    ProotDistro,
    RuntimeConfig,
    UnsupportedOperationError,
)

_ROOT = Path(__file__).resolve().parents[2]
_GENERIC = {
    "InvalidArgumentError", "OperationCancelledError", "OperationError",
    "SessionStartupTimeoutError",
}
_ERRORS = {
    cls.__name__: cls for cls in (
        AlreadyExistsError, AuthenticationError, BusyError, IntegrityError,
        NetworkError, NotFoundError, PartialOperationError,
        UnsupportedOperationError,
    )
}

#: Pairs shared with a verified path but not provokable offline.
STATIC_ONLY = {
    ("build", "AuthenticationError"):
        "a private base image needs an https registry that answers 401",
    ("reset_container", "AuthenticationError"):
        "reset re-pulls through install, whose 401 path is verified",
    ("reset_container", "IntegrityError"):
        "reset re-pulls through install, whose corrupt-content path is "
        "verified",
    ("move", "PartialOperationError"):
        "move shares copy's per-entry failure accounting",
    ("search", "NetworkError"):
        "needs Docker Hub; covered by unit tests with a stubbed transport",
}

#: Filled in by the scenario table below: (method, error) -> scenario.
VERIFIED = {}


def _scenario(method, error):
    def register(function):
        VERIFIED[(method, error)] = function
        return function
    return register


def _expected_errors(method):
    errors = {error for (name, error) in VERIFIED if name == method}
    errors |= {error for (name, error) in STATIC_ONLY if name == method}
    return errors


_METHODS = sorted({name for name, _ in VERIFIED} | {
    name for name, _ in STATIC_ONLY
})


class _Registry(http.server.BaseHTTPRequestHandler):
    """A registry that refuses everything with 401."""

    def log_message(self, *_args):
        pass

    def do_GET(self):
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="contract"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = do_POST = do_PUT = do_PATCH = do_GET


_HELPER = """
import os, sys, time
os.environ["_PROOT_DISTRO_API_RUNTIME_DIR"] = {runtime!r}
os.environ["_PROOT_DISTRO_API_CACHE_DIR"] = {cache!r}
sys.path.insert(0, {root!r})
from proot_distro.locking import BuildLock, ContainerLock
locks = [
    ContainerLock("box", exclusive=True, command="contract"),
    ContainerLock("ocibox", exclusive=True, command="contract"),
    BuildLock("contract/locked:1", {arch!r}, command="contract"),
]
for lock in locks:
    assert lock.acquire(), "helper could not take a lock"
print("ready", flush=True)
time.sleep(300)
"""


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    import _builders as builders

    tmp = tmp_path_factory.mktemp("error-contract")
    fake_proot = tmp / "fake-proot"
    fake_proot.write_text("#!/bin/sh\nexit 0\n")
    fake_proot.chmod(0o755)
    runtime, cache = tmp / "rt", tmp / "cache"
    pd = ProotDistro(RuntimeConfig(runtime, cache, fake_proot))

    archive = tmp / "rootfs.tar"
    builders.make_tar(str(archive), builders.rootfs_members([
        {"name": "etc/hello", "type": "file", "data": b"hello"},
    ]))
    corrupt = tmp / "corrupt.tar"
    corrupt.write_bytes(b"garbage" * 100)
    junk_archive = tmp / "junk.tar.gz"
    junk_archive.write_bytes(b"not an archive at all" * 50)
    fake_zstd = tmp / "fake.tar.zst"
    fake_zstd.write_bytes(b"\x28\xb5\x2f\xfd" + b"x" * 100)

    for name in ("box", "spare", "other"):
        pd.install(archive, name=name)
    box_backup = tmp / "box.tar.gz"
    pd.backup("box", box_backup)

    context = tmp / "context"
    context.mkdir()
    (context / "data").write_text("data")
    (context / "Dockerfile").write_text("FROM scratch\nCOPY data /data\n")
    server = http.server.HTTPServer(("127.0.0.1", 0), _Registry)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    refused = f"127.0.0.1:{server.server_port}/contract/img:1"
    unreachable = "127.0.0.1:1/contract/img:1"
    # Refs used to *install* must stay out of the local cache, or the
    # install is served from it and never touches the network.
    absent_refused = f"127.0.0.1:{server.server_port}/contract/absent:1"
    absent_unreachable = "127.0.0.1:1/contract/absent:1"
    pd.build(context, tags=["contract/locked:1"], install_as="ocibox")
    pd.build(context, tags=[refused])
    pd.build(context, tags=[unreachable])
    # reset re-pulls its recorded image, so that image must not be cached.
    reset_ref = "127.0.0.1:1/contract/reset:1"
    pd.build(context, tags=[reset_ref], install_as="refbox")
    pd.remove_image(reset_ref)
    arch = pd.list_images()[0].architecture

    unreadable = tmp / "unreadable"
    unreadable.mkdir()
    (unreadable / "ok").write_text("1")
    (unreadable / "denied").write_text("2")
    (unreadable / "denied").chmod(0)

    helper = subprocess.Popen(
        [sys.executable, "-c", _HELPER.format(
            runtime=str(runtime), cache=str(cache), root=str(_ROOT),
            arch=arch,
        )],
        stdout=subprocess.PIPE, text=True,
    )
    try:
        assert helper.stdout.readline().strip() == "ready"
        yield SimpleNamespace(
            pd=pd, tmp=tmp, archive=archive, corrupt=corrupt,
            junk_archive=junk_archive, fake_zstd=fake_zstd,
            box_backup=box_backup, context=context, refused=refused,
            unreachable=unreachable, unreadable=unreadable,
            absent_refused=absent_refused,
            absent_unreachable=absent_unreachable,
        )
    finally:
        helper.kill()
        helper.wait()
        (unreadable / "denied").chmod(0o600)
        server.shutdown()


def _zstd_available():
    try:
        import compression.zstd  # noqa: F401
    except ImportError:
        return False
    return True


needs_no_zstd = pytest.mark.skipif(
    _zstd_available(), reason="zstd exists, so it is not 'unsupported'"
)
unprivileged = pytest.mark.skipif(
    os.geteuid() == 0, reason="root can read the 'unreadable' file"
)


# --- scenarios: each must raise the named error through a real worker ---

@_scenario("install", "AlreadyExistsError")
def _(e): e.pd.install(e.archive, name="spare")
@_scenario("install", "NotFoundError")
def _(e): e.pd.install(e.tmp / "missing.tar", name="fresh1")
@_scenario("install", "NetworkError")
def _(e): e.pd.install(
    e.absent_unreachable, name="fresh2", allow_insecure=True
)
@_scenario("install", "AuthenticationError")
def _(e): e.pd.install(
    e.absent_refused, name="fresh3", allow_insecure=True
)
@_scenario("install", "IntegrityError")
def _(e): e.pd.install(e.corrupt, name="fresh4")
@_scenario("install", "BusyError")
def _(e): e.pd.install(e.archive, name="box")

@_scenario("remove_container", "NotFoundError")
def _(e): e.pd.remove_container("ghost")
@_scenario("remove_container", "BusyError")
def _(e): e.pd.remove_container("box")

@_scenario("rename_container", "NotFoundError")
def _(e): e.pd.rename_container("ghost", "ghost2")
@_scenario("rename_container", "AlreadyExistsError")
def _(e): e.pd.rename_container("spare", "other")
@_scenario("rename_container", "BusyError")
def _(e): e.pd.rename_container("box", "boxr")

@_scenario("reset_container", "NotFoundError")
def _(e): e.pd.reset_container("ghost")
@_scenario("reset_container", "UnsupportedOperationError")
def _(e): e.pd.reset_container("spare")
@_scenario("reset_container", "BusyError")
def _(e): e.pd.reset_container("ocibox")
@_scenario("reset_container", "NetworkError")
def _(e): e.pd.reset_container("refbox")

@_scenario("copy", "NotFoundError")
def _(e): e.pd.copy(e.archive, "ghost:/tmp/")
@_scenario("copy", "BusyError")
def _(e): e.pd.copy("box:/etc/hello", e.tmp / "out")
@_scenario("copy", "PartialOperationError")
@unprivileged
def _(e): e.pd.copy(e.unreadable, "spare:/tmp/", recursive=True)

@_scenario("move", "NotFoundError")
def _(e): e.pd.move(e.tmp / "missing", "spare:/tmp/")
@_scenario("move", "BusyError")
def _(e): e.pd.move("box:/etc/hello", e.tmp / "moved")

@_scenario("sync", "NotFoundError")
def _(e): e.pd.sync(e.tmp / "missing", "spare:/tmp/")
@_scenario("sync", "BusyError")
def _(e): e.pd.sync(e.unreadable, "box:/tmp/sync")
@_scenario("sync", "PartialOperationError")
@unprivileged
def _(e): e.pd.sync(e.unreadable, "spare:/tmp/sync")

@_scenario("backup", "NotFoundError")
def _(e): e.pd.backup("ghost", e.tmp / "b-ghost.tar.gz")
@_scenario("backup", "AlreadyExistsError")
def _(e): e.pd.backup("spare", e.box_backup)
@_scenario("backup", "UnsupportedOperationError")
@needs_no_zstd
def _(e): e.pd.backup("spare", e.tmp / "b.tar.zst")
@_scenario("backup", "BusyError")
def _(e): e.pd.backup("box", e.tmp / "b-box.tar.gz")

@_scenario("restore", "NotFoundError")
def _(e): e.pd.restore(e.tmp / "missing.tar.gz")
@_scenario("restore", "IntegrityError")
def _(e): e.pd.restore(e.junk_archive)
@_scenario("restore", "UnsupportedOperationError")
@needs_no_zstd
def _(e): e.pd.restore(e.fake_zstd)
@_scenario("restore", "BusyError")
def _(e): e.pd.restore(e.box_backup)

@_scenario("build", "NotFoundError")
def _(e): e.pd.build(e.tmp / "no-context", tags=["contract/x:1"])
@_scenario("build", "AlreadyExistsError")
def _(e): e.pd.build(e.context, tags=["contract/x:1"], install_as="spare")
@_scenario("build", "NetworkError")
def _(e):
    (e.tmp / "net").mkdir(exist_ok=True)
    (e.tmp / "net" / "Dockerfile").write_text(
        "FROM 127.0.0.1:1/contract/base:1\n"
    )
    e.pd.build(e.tmp / "net", tags=["contract/net:1"])
@_scenario("build", "BusyError")
def _(e): e.pd.build(e.context, tags=["contract/locked:1"])

@_scenario("push", "NotFoundError")
def _(e): e.pd.push("contract/uncached:1")
@_scenario("push", "AuthenticationError")
def _(e): e.pd.push(e.refused, allow_insecure=True)
@_scenario("push", "NetworkError")
def _(e): e.pd.push(e.unreachable, allow_insecure=True)
@_scenario("push", "BusyError")
def _(e): e.pd.push("contract/locked:1")

@_scenario("remove_image", "NotFoundError")
def _(e): e.pd.remove_image("contract/uncached:1")
@_scenario("remove_image", "BusyError")
def _(e): e.pd.remove_image("contract/locked:1")

@_scenario("clear_cache", "BusyError")
def _(e): e.pd.clear_cache(orphan=True)

for _method, _call in (
    ("start", lambda pd, name: pd.start(name, ["true"])),
    ("run", lambda pd, name: pd.run(name)),
    ("start_detached", lambda pd, name: pd.start_detached(name)),
    ("run_detached", lambda pd, name: pd.run_detached(name)),
    ("inspect_start", lambda pd, name: pd.inspect_start(name)),
    ("inspect_run", lambda pd, name: pd.inspect_run(name)),
):
    VERIFIED[(_method, "NotFoundError")] = (
        lambda e, _call=_call: _call(e.pd, "ghost")
    )
    VERIFIED[(_method, "BusyError")] = (
        lambda e, _call=_call: _call(e.pd, "ocibox")
    )

_METHODS = sorted({name for name, _ in VERIFIED} | {
    name for name, _ in STATIC_ONLY
})


#: Refused connections are retried with a fixed delay (about 20 s each), so
#: these scenarios run concurrently, once, and the tests read the outcome.
SLOW_NETWORK = {
    ("install", "NetworkError"),
    ("reset_container", "NetworkError"),
    ("build", "NetworkError"),
    ("push", "NetworkError"),
}


@pytest.fixture(scope="module")
def network_outcomes(env):
    from concurrent.futures import ThreadPoolExecutor

    def attempt(key):
        try:
            VERIFIED[key](env)
        except BaseException as exc:  # recorded, asserted by the test
            return exc
        return None

    keys = sorted(SLOW_NETWORK)
    with ThreadPoolExecutor(max_workers=len(keys)) as executor:
        return dict(zip(keys, executor.map(attempt, keys)))


def _skip_marks(function):
    return [
        mark for mark in getattr(function, "pytestmark", [])
        if mark.name == "skipif"
    ]


@pytest.mark.parametrize(
    "method,error", sorted(VERIFIED), ids=lambda value: str(value),
)
def test_method_raises_the_documented_error(
    env, request, method, error,
):
    scenario = VERIFIED[(method, error)]
    for mark in _skip_marks(scenario):
        if mark.args[0]:
            pytest.skip(mark.kwargs.get("reason", "not applicable"))

    if (method, error) in SLOW_NETWORK:
        raised = request.getfixturevalue("network_outcomes")[(method, error)]
        assert raised is not None, f"{method} did not raise {error}"
    else:
        with pytest.raises(_ERRORS[error]) as exc:
            scenario(env)
        raised = exc.value

    # Exactly the documented class, not a different typed error.
    assert type(raised) is _ERRORS[error], repr(raised)
    assert raised.operation == method


def _docstring_errors(method):
    doc = inspect.getdoc(getattr(ProotDistro, method)) or ""
    section = doc.split("Raises:", 1)[1] if "Raises:" in doc else ""
    names = re.findall(r"^\s{4}([A-Z][A-Za-z]+Error):", section, re.M)
    return set(names) - _GENERIC


@pytest.mark.parametrize("method", _METHODS)
def test_docstring_lists_exactly_the_verified_errors(method):
    assert _docstring_errors(method) == _expected_errors(method)


def test_every_documented_error_is_checked_for_each_method():
    """A method with a Raises section must be covered by this contract."""
    for name, member in inspect.getmembers(ProotDistro, inspect.isfunction):
        if name.startswith("_"):
            continue
        documented = _docstring_errors(name)
        assert documented == _expected_errors(name), name


def test_api_guide_table_matches_the_contract():
    guide = (_ROOT / "API.md").read_text(encoding="utf-8")
    table = guide.split("### Errors by method", 1)[1].split("## ", 1)[0]
    rows = {}
    for line in table.splitlines():
        match = re.match(r"^\| `(\w+)` \| (.+?) \|$", line)
        if match:
            rows[match.group(1)] = {
                item.strip() for item in match.group(2).split(",")
            }
    assert rows == {m: _expected_errors(m) for m in _METHODS}


def test_static_only_pairs_have_a_verified_sibling():
    """Each unproven claim must borrow from a proven path of the same kind."""
    for (method, error), reason in STATIC_ONLY.items():
        assert reason
        assert any(error == other for (_m, other) in VERIFIED), (
            method, error,
        )
