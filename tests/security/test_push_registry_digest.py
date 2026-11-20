# The digest `push` reports for the manifest it published.
#
# A registry answers a manifest upload with a Docker-Content-Digest
# header, and push used to hand that header on as the pushed digest
# without looking at it: the CLI printed it to the terminal and the API
# returned it as PushResult.manifest_digest. The registry chose those
# bytes, and so does anything between it and the client under
# --allow-insecure. An escape sequence in the header retitled or repainted
# the user's terminal, and whatever digest it named, true or not, was
# reported as the one pushed.
#
# The digest reported is now the one push works out from the manifest
# bytes it sent, as `docker push` does. A header, when the registry sends
# one, must name that same digest; any other answer ends the push in an
# IntegrityError that never repeats a header that is not a digest.

import hashlib
from types import SimpleNamespace

import pytest

from _registry import CA_FILE, FakeRegistry
from proot_distro.api import (
    IntegrityError, ProotDistro, RegistryCredentials,
)
from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.push import command_push
from proot_distro.helpers.docker import transport
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import (
    OCI_CONFIG_MEDIA, OCI_LAYER_MEDIA, OCI_MANIFEST_MEDIA, canonical_json,
)

# An OSC that retitles the terminal, then a C1 CSI that clears it: the
# header is decoded as Latin-1, so a registry's byte 0x9b arrives as
# U+009B, which a terminal takes for ESC [.
HOSTILE = "\x1b]0;pwned\x07\x9b2J"
CONTROLS = ("\x1b", "\x07", "\x9b")
ANOTHER = "sha256:" + "f" * 64


def _sha256(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


@pytest.fixture
def registry(monkeypatch):
    """A TLS registry whose certificate the client trusts."""
    monkeypatch.setenv("no_proxy", "*")
    monkeypatch.setenv("SSL_CERT_FILE", CA_FILE)
    # The shared openers read the trust store when they are made.
    monkeypatch.setattr(transport, "_verified_opener",
                        transport._build_opener(False))
    monkeypatch.setattr(transport, "_insecure_opener", None)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    reg = FakeRegistry()
    yield reg
    reg.close()


@pytest.fixture
def pushable(registry, builders):
    """A reference `build` left in the caches, pushable to *registry*."""
    ref = f"{registry.host()}/team/app:1"
    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "etc/motd", "type": "file", "data": b"hello\n"}])
    config = {"os": "linux", "rootfs": {"type": "layers",
                                        "diff_ids": [diff_id]}}
    config_bytes = canonical_json(config)
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA,
        "config": {"mediaType": OCI_CONFIG_MEDIA, "size": len(config_bytes),
                   "digest": _sha256(config_bytes)},
        "layers": [{"mediaType": OCI_LAYER_MEDIA, "size": size,
                    "digest": digest}],
    }
    save_manifest_cache(ref, get_device_cpu_arch(), manifest, "team/app",
                        config)
    return ref


def _stored_digest(registry):
    """The digest of the manifest the registry stored under the tag."""
    body, _media = registry.manifests[("team/app", "1")]
    return _sha256(body)


def _cli_push(ref):
    command_push(SimpleNamespace(
        image_ref=ref, override_arch=None, quiet=False,
        allow_insecure=False,
    ))


def _assert_no_controls(text):
    for control in CONTROLS:
        assert control not in text


def test_a_hostile_digest_header_never_reaches_the_terminal(
        registry, pushable, capsys):
    registry.manifest_digest_header = HOSTILE
    with pytest.raises(SystemExit) as exc:
        _cli_push(pushable)
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    _assert_no_controls(out + err)
    assert "malformed digest" in err
    # The registry took the upload, so the message names what was sent.
    assert _stored_digest(registry) in err


def test_the_api_refuses_a_hostile_digest_header(registry, pushable):
    registry.manifest_digest_header = HOSTILE
    with pytest.raises(IntegrityError) as exc:
        ProotDistro(credentials=RegistryCredentials(
            "alice", "s3cret")).push(pushable)
    assert exc.value.operation == "push"
    _assert_no_controls(str(exc.value))
    assert "malformed digest" in str(exc.value)


def test_a_registry_naming_another_digest_is_refused(
        registry, pushable, capsys):
    # A well-formed digest that is not the manifest's: reporting either
    # one as "the digest pushed" could be wrong, so neither is.
    registry.manifest_digest_header = ANOTHER
    with pytest.raises(SystemExit) as exc:
        _cli_push(pushable)
    assert exc.value.code == 1
    out, err = capsys.readouterr()
    assert ANOTHER in err
    assert _stored_digest(registry) in err
    assert "Digest:" not in out + err


def test_the_api_refuses_another_digest(registry, pushable):
    registry.manifest_digest_header = ANOTHER
    with pytest.raises(IntegrityError) as exc:
        ProotDistro(credentials=RegistryCredentials(
            "alice", "s3cret")).push(pushable)
    assert ANOTHER in str(exc.value)
    assert _stored_digest(registry) in str(exc.value)


def test_a_digest_spelled_out_of_the_grammar_is_not_repeated(
        registry, pushable, capsys):
    # The algorithm and hex may hold nothing but their own characters,
    # and the hex is no longer than a sha512's: anything else is called
    # malformed instead of being printed.
    registry.manifest_digest_header = "sha256:" + "a" * 4096
    with pytest.raises(SystemExit):
        _cli_push(pushable)
    err = capsys.readouterr().err
    assert "a" * 4096 not in err
    assert "malformed digest" in err


@pytest.mark.parametrize("header", [None, ""], ids=["sent", "omitted"])
def test_the_digest_reported_is_the_manifests_own(
        registry, pushable, capsys, header):
    # The registry's own digest, or none at all (the header is optional):
    # either way what is reported is the digest of what was sent, which
    # is what the registry stored.
    registry.manifest_digest_header = header
    _cli_push(pushable)
    err = capsys.readouterr().err
    assert f"Digest:     {_stored_digest(registry)}" in err

    result = ProotDistro(credentials=RegistryCredentials(
        "alice", "s3cret")).push(pushable)
    assert result.manifest_digest == _stored_digest(registry)
