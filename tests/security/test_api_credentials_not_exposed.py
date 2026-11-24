"""Registry credentials must reach a worker only through private channels.

An argv entry is readable by every local user (``/proc/<pid>/cmdline``) and
an inherited environment by the same user's other processes, so neither may
ever carry a password the caller handed to the API. The credentials cross
the request pipe or the credential socket instead.
"""

import http.server
import os
import subprocess
import threading

import pytest

from proot_distro.api import (
    AuthenticationError,
    EventKind,
    ProotDistro,
    RegistryCredentials,
)

SECRET = "pw-9f3a1c-never-in-argv"
USER = "api-user-7e2b"


class _Refuse(http.server.BaseHTTPRequestHandler):
    seen = []

    def log_message(self, *_args):
        pass

    def do_GET(self):
        _Refuse.seen.append(self.headers.get("Authorization"))
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="x"')
        self.send_header("Content-Length", "0")
        self.end_headers()

    do_HEAD = do_GET


@pytest.fixture
def registry():
    _Refuse.seen = []
    server = http.server.HTTPServer(("127.0.0.1", 0), _Refuse)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    yield f"127.0.0.1:{server.server_port}/secret/image:1"
    server.shutdown()


@pytest.fixture
def spawned(monkeypatch):
    """Record argv, environment and /proc view of every spawned worker."""
    real = subprocess.Popen
    records = []

    class Spy(real):
        def __init__(self, args, *a, **kw):
            super().__init__(args, *a, **kw)
            record = {"argv": list(args), "env": dict(kw.get("env") or {})}
            for name in ("cmdline", "environ"):
                try:
                    with open(f"/proc/{self.pid}/{name}", "rb") as fh:
                        record[name] = fh.read()
                except OSError:
                    record[name] = b""
            records.append(record)

    monkeypatch.setattr(subprocess, "Popen", Spy)
    return records


def _assert_secret_absent(records):
    assert records, "no worker was started"
    needle = SECRET.encode()
    for record in records:
        assert not any(SECRET in str(arg) for arg in record["argv"])
        assert not any(SECRET in str(v) for v in record["env"].values())
        assert needle not in record["cmdline"]
        assert needle not in record["environ"]
        assert "PD_DOCKER_AUTH" not in record["env"]


def test_explicit_credentials_never_reach_argv_or_environment(
        registry, spawned):
    events = []
    pd = ProotDistro(
        event_handler=events.append,
        credentials=RegistryCredentials(USER, SECRET),
    )

    with pytest.raises(AuthenticationError) as exc:
        pd.install(registry, name="secretbox", allow_insecure=True)

    _assert_secret_absent(spawned)
    # The secret was used (so the test exercises the real path) but is not
    # echoed back in the error or any progress message.
    assert any(seen and seen.startswith("Basic ") for seen in _Refuse.seen)
    assert SECRET not in str(exc.value)
    assert SECRET not in repr(exc.value)
    assert all(SECRET not in event.message for event in events)
    assert all(
        SECRET not in str(getattr(event, field, ""))
        for event in events for field in ("resource", "phase")
    )


def test_provider_credentials_never_reach_argv_or_environment(
        registry, spawned):
    def provider(request):
        return RegistryCredentials(USER, SECRET)

    pd = ProotDistro(credential_provider=provider)
    with pytest.raises(AuthenticationError):
        pd.install(registry, name="secretbox2", allow_insecure=True)

    _assert_secret_absent(spawned)
    assert any(seen and seen.startswith("Basic ") for seen in _Refuse.seen)


def test_ambient_registry_password_is_not_forwarded(
        registry, spawned, monkeypatch):
    monkeypatch.setenv("PD_DOCKER_AUTH", f"{USER}:{SECRET}")

    with pytest.raises(AuthenticationError):
        ProotDistro().install(registry, name="ambient", allow_insecure=True)

    _assert_secret_absent(spawned)
    # Nothing authenticated: the ambient password was not used either.
    assert not any(seen and seen.startswith("Basic ") for seen in _Refuse.seen)


def test_credentials_repr_hides_the_secret():
    shown = repr(RegistryCredentials(USER, SECRET))
    assert SECRET not in shown
    assert SECRET not in str(RegistryCredentials(USER, SECRET))


def test_a_worker_credential_never_reaches_a_build_run_step(monkeypatch):
    """The host-side exec for RUN gets a fresh environment, not the worker's."""
    from types import SimpleNamespace
    from proot_distro.helpers.build_engine import run_step

    stage = SimpleNamespace(
        env={}, args={}, declared_args=set(), image_config={"config": {}},
        rootfs_dir="", base_ref="", base_image_id="", rootfs_fd=None,
    )
    monkeypatch.setenv("PD_DOCKER_AUTH", f"{USER}:{SECRET}")
    env = run_step._build_child_env(
        SimpleNamespace(warned_host_exec=set()), stage
    )
    assert "PD_DOCKER_AUTH" not in env
    assert not any(SECRET in value for value in env.values())
