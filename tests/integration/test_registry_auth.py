# The pull and push pipelines end to end against a registry that wants
# authentication: tests/_registry.py's FakeRegistry, served over TLS from
# 127.0.0.1, with nothing in between mocked. The /v2/ probe, the Bearer
# challenge, the token exchange carrying PD_DOCKER_AUTH as Basic
# credentials, the Bearer token on every registry request, blob GETs
# redirected to a storage origin that refuses credentials, and the
# refusal each step can end in.
#
# A registry may also check passwords itself (a Basic challenge, no token
# service): PD_DOCKER_AUTH then goes to the registry as it is, and still
# nowhere else.
#
# Beyond whether the pull or push worked, every test checks who saw which
# credential (_assert_credentials_in_place): the password only ever goes
# to the token service -- or to a registry that asked for it itself --
# and the token only ever to the registry.

import hashlib
import os
from types import SimpleNamespace

import pytest

from _builders import pull_image_into
from _registry import CA_FILE, FakeRegistry, basic
from proot_distro import constants
from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.install import command_install
from proot_distro.commands.push import command_push
from proot_distro.helpers.docker import transport
from proot_distro.helpers.docker.cache import save_manifest_cache
from proot_distro.helpers.docker.media import (
    OCI_CONFIG_MEDIA, OCI_LAYER_MEDIA, OCI_MANIFEST_MEDIA, canonical_json,
)
from proot_distro.helpers.docker.push import push_image
from proot_distro.paths import container_dir, container_rootfs

ALICE = basic("alice", "s3cret")
FILES = [{"name": "etc/motd", "type": "file", "data": b"private bits\n"}]
CONFIG = {"os": "linux", "config": {"Env": ["FROM_THE_IMAGE=1"]}}


@pytest.fixture
def no_proxy(monkeypatch):
    # The openers are built at import with whatever proxy the environment
    # names; one must not stand between the client and 127.0.0.1.
    monkeypatch.setenv("no_proxy", "*")


def _rebuild_openers(monkeypatch):
    # Since Python 3.12 an HTTPSHandler makes its default context when the
    # handler is made -- at import, for the transport's shared openers --
    # and the trust store SSL_CERT_FILE names is read then. They are made
    # again here, the way the module makes them, under the environment
    # the test has set.
    monkeypatch.setattr(transport, "_verified_opener",
                        transport._build_opener(False))
    monkeypatch.setattr(transport, "_insecure_opener", None)


@pytest.fixture
def registry(no_proxy, monkeypatch):
    """A TLS registry whose certificate the client trusts."""
    monkeypatch.setenv("SSL_CERT_FILE", CA_FILE)
    _rebuild_openers(monkeypatch)
    reg = FakeRegistry()
    yield reg
    reg.close()


@pytest.fixture
def basic_registry(no_proxy, monkeypatch):
    """A trusted TLS registry that checks passwords itself (Basic)."""
    monkeypatch.setenv("SSL_CERT_FILE", CA_FILE)
    _rebuild_openers(monkeypatch)
    reg = FakeRegistry(scheme="basic")
    yield reg
    reg.close()


@pytest.fixture(params=["bearer", "basic"])
def untrusted_registry(request, no_proxy, monkeypatch):
    """A TLS registry whose certificate is signed by nobody it trusts."""
    monkeypatch.delenv("SSL_CERT_FILE", raising=False)
    _rebuild_openers(monkeypatch)
    reg = FakeRegistry(scheme=request.param)
    yield reg
    reg.close()


@pytest.fixture(params=["bearer", "basic"])
def http_registry(request, no_proxy):
    """A registry -- token service and storage too -- on plain HTTP."""
    reg = FakeRegistry(tls=False, scheme=request.param)
    yield reg
    reg.close()


@pytest.fixture
def as_docker_hub(monkeypatch):
    """Make *reg* answer for Docker Hub and its token service."""
    def point(reg):
        monkeypatch.setattr(transport, "REGISTRY_URL", reg.url("registry"))
        monkeypatch.setattr(transport, "AUTH_URL", reg.url("auth") + "/token")
    return point


def _assert_credentials_in_place(reg, password=ALICE):
    """The password reached the token service only, the token the registry.

    *password* is what the client authenticated with: ALICE, or '' for an
    anonymous one. The /v2/ probe goes bare. On a Bearer registry every
    other registry request carries a token the service issued, and only
    the token service sees the password; on a Basic one the registry
    gets the password itself and there is no token service to see it.
    Storage gets nothing, either way.
    """
    tokens = reg.issued()
    for seen in reg.seen():
        if seen.role == "auth":
            assert reg.scheme == "bearer", seen
            assert seen.authorization == password, seen
        elif seen.role == "registry" and seen.path == "/v2/":
            assert seen.authorization == "", seen
        elif seen.role == "registry" and reg.scheme == "basic":
            assert seen.authorization == password, seen
        elif seen.role == "registry":
            assert seen.authorization in tokens, seen
        else:
            assert seen.authorization == "", seen


def _rootfs(tmp_path):
    root = tmp_path / "rootfs"
    root.mkdir()
    return root


def _built(builders, ref, arch, repo):
    """Leave *ref* in the caches the way `build` does."""
    digest, size, diff_id = builders.seed_cached_layer(FILES)
    config = dict(CONFIG, rootfs={"type": "layers", "diff_ids": [diff_id]})
    config_bytes = canonical_json(config)
    manifest = {
        "schemaVersion": 2,
        "mediaType": OCI_MANIFEST_MEDIA,
        "config": {"mediaType": OCI_CONFIG_MEDIA, "size": len(config_bytes),
                   "digest": _sha256(config_bytes)},
        "layers": [{"mediaType": OCI_LAYER_MEDIA, "size": size,
                    "digest": digest}],
    }
    save_manifest_cache(ref, arch, manifest, repo, config)
    return manifest


def _sha256(data):
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _empty_caches():
    for d in (constants.MANIFEST_CACHE_DIR, constants.LAYER_CACHE_DIR):
        for name in os.listdir(d):
            os.remove(os.path.join(d, name))


def _push_args(ref, **over):
    return SimpleNamespace(**dict(
        image_ref=ref, override_arch=None, quiet=True, allow_insecure=False,
        **over))


# ----- pull ----------------------------------------------------------------

@pytest.mark.parametrize("keys", [
    ("token", "access_token"),      # what most token services send
    ("token",),
    ("access_token",),              # the OAuth2 name alone
])
def test_a_private_image_is_pulled_with_credentials(
        registry, monkeypatch, tmp_path, keys):
    registry.grant_keys = keys
    registry.add_image("team/private", "1", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    root = _rootfs(tmp_path)
    meta = pull_image_into(f"{registry.host()}/team/private:1", str(root),
                           "x86_64")
    assert (root / "etc" / "motd").read_bytes() == b"private bits\n"
    # The config comes through the same redirect as the layer, and a
    # config that cannot be fetched is quietly {} for a tag -- so this is
    # what shows the token, and the stripping, worked for it too.
    assert meta["image_config"]["config"]["Env"] == ["FROM_THE_IMAGE=1"]
    assert registry.token_scopes() == ["repository:team/private:pull"]
    assert len(registry.seen("cdn")) == 2           # config and layer
    _assert_credentials_in_place(registry)


def test_a_private_image_without_credentials_is_refused(registry, capsys):
    registry.add_image("team/private", "1", FILES, CONFIG)
    with pytest.raises(SystemExit) as exc:
        command_install(SimpleNamespace(
            image_ref=f"{registry.host()}/team/private:1",
            custom_container_name="box", override_arch=None))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "does not exist or is a private image" in err
    assert "PD_DOCKER_AUTH" in err
    assert not os.path.lexists(container_dir("box"))
    # Asked anonymously, got a token granting nothing, and was refused
    # the manifest with it.
    assert registry.token_scopes() == ["repository:team/private:pull"]
    assert registry.seen("cdn") == []
    _assert_credentials_in_place(registry, password="")


def test_a_wrong_password_is_refused_before_the_registry_is_asked(
        registry, monkeypatch, tmp_path):
    registry.add_image("team/private", "1", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:wrong")
    root = _rootfs(tmp_path)
    with pytest.raises(RuntimeError) as exc:
        pull_image_into(f"{registry.host()}/team/private:1", str(root),
                        "x86_64")
    assert "Access denied" in str(exc.value)
    assert "HTTP 401" in str(exc.value)
    assert "PD_DOCKER_AUTH" in str(exc.value)
    assert os.listdir(root) == []
    assert [s.path for s in registry.seen("registry")] == ["/v2/"]
    assert registry.issued() == set()
    _assert_credentials_in_place(registry, password=basic("alice", "wrong"))


def test_a_public_image_is_pulled_anonymously(registry, tmp_path):
    registry.public.add("team/public")
    registry.add_image("team/public", "1", FILES, CONFIG)
    root = _rootfs(tmp_path)
    pull_image_into(f"{registry.host()}/team/public:1", str(root), "x86_64")
    assert (root / "etc" / "motd").read_bytes() == b"private bits\n"
    _assert_credentials_in_place(registry, password="")


def test_missing_layers_of_a_cached_manifest_are_fetched_with_credentials(
        registry, monkeypatch, tmp_path):
    # The manifest is cached and its layer is not: only a token is asked
    # for, and the layer fetched with it.
    image = registry.add_image("team/private", "1", FILES, CONFIG)
    ref = f"{registry.host()}/team/private:1"
    save_manifest_cache(ref, "x86_64", image["manifest"], "team/private",
                        image["config"])
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    root = _rootfs(tmp_path)
    pull_image_into(ref, str(root), "x86_64")
    assert (root / "etc" / "motd").read_bytes() == b"private bits\n"
    assert registry.token_scopes() == ["repository:team/private:pull"]
    assert not any("/manifests/" in s.path for s in registry.seen())
    assert len(registry.seen("cdn")) == 1
    _assert_credentials_in_place(registry)


def test_missing_layers_of_a_cached_manifest_with_a_wrong_password(
        registry, monkeypatch, tmp_path):
    image = registry.add_image("team/private", "1", FILES, CONFIG)
    ref = f"{registry.host()}/team/private:1"
    save_manifest_cache(ref, "x86_64", image["manifest"], "team/private",
                        image["config"])
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:wrong")
    with pytest.raises(RuntimeError) as exc:
        pull_image_into(ref, str(_rootfs(tmp_path)), "x86_64")
    assert "Access denied" in str(exc.value)
    assert [s.path for s in registry.seen("registry")] == ["/v2/"]


def test_a_layer_refused_mid_pull_is_access_denied(
        registry, monkeypatch, tmp_path):
    # The token got the manifest, and the registry then refuses the blobs
    # -- a token revoked, or one scoped narrower than the image needs.
    registry.add_image("team/private", "1", FILES, CONFIG)
    registry.deny.add("blob")
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    root = _rootfs(tmp_path)
    with pytest.raises(RuntimeError) as exc:
        pull_image_into(f"{registry.host()}/team/private:1", str(root),
                        "x86_64")
    assert "Access denied" in str(exc.value)
    assert "Network error" not in str(exc.value)
    assert registry.seen("cdn") == []
    _assert_credentials_in_place(registry)


def test_a_docker_hub_pull_authenticates_at_the_hub_token_service(
        registry, as_docker_hub, monkeypatch, tmp_path):
    as_docker_hub(registry)
    registry.add_image("library/alpine", "latest", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    root = _rootfs(tmp_path)
    pull_image_into("alpine", str(root), "x86_64")
    assert (root / "etc" / "motd").read_bytes() == b"private bits\n"
    assert registry.token_scopes() == ["repository:library/alpine:pull"]
    assert "service=registry.docker.io" in registry.seen("auth")[0].path
    # The token service is known; Docker Hub is never probed for it.
    assert "/v2/" not in [s.path for s in registry.seen("registry")]
    _assert_credentials_in_place(registry)


# ----- transport security --------------------------------------------------

def test_an_untrusted_certificate_gets_no_credentials(
        untrusted_registry, monkeypatch, tmp_path):
    reg = untrusted_registry
    reg.add_image("team/private", "1", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    with pytest.raises(RuntimeError) as exc:
        pull_image_into(f"{reg.host()}/team/private:1",
                        str(_rootfs(tmp_path)), "x86_64")
    assert "--allow-insecure" in str(exc.value)
    # Refused in the TLS handshake: not one HTTP request was made.
    assert reg.seen() == []


def test_allow_insecure_reaches_a_registry_with_an_untrusted_certificate(
        untrusted_registry, monkeypatch, tmp_path):
    reg = untrusted_registry
    reg.add_image("team/private", "1", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    root = _rootfs(tmp_path)
    pull_image_into(f"{reg.host()}/team/private:1", str(root), "x86_64",
                    insecure=True)
    assert (root / "etc" / "motd").read_bytes() == b"private bits\n"
    _assert_credentials_in_place(reg)


def test_allow_insecure_does_not_unverify_docker_hub(
        untrusted_registry, as_docker_hub, monkeypatch, tmp_path):
    # --allow-insecure is for registries the user names. Docker Hub's
    # token exchange -- the request with the password in it -- stays on
    # verified TLS, so an impostor's certificate stops it cold.
    reg = untrusted_registry
    as_docker_hub(reg)
    reg.add_image("library/alpine", "latest", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    with pytest.raises(RuntimeError):
        pull_image_into("alpine", str(_rootfs(tmp_path)), "x86_64",
                        insecure=True)
    assert reg.seen() == []


def test_a_plain_http_registry_gets_no_credentials_without_allow_insecure(
        http_registry, monkeypatch, tmp_path):
    reg = http_registry
    reg.add_image("team/private", "1", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    with pytest.raises(RuntimeError) as exc:
        pull_image_into(f"{reg.host()}/team/private:1",
                        str(_rootfs(tmp_path)), "x86_64")
    assert "--allow-insecure" in str(exc.value)
    assert reg.seen("auth") == []
    assert all(s.authorization == "" for s in reg.seen())


def test_allow_insecure_pulls_from_a_plain_http_registry_with_credentials(
        http_registry, monkeypatch, tmp_path):
    reg = http_registry
    reg.add_image("team/private", "1", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    root = _rootfs(tmp_path)
    pull_image_into(f"{reg.host()}/team/private:1", str(root), "x86_64",
                    insecure=True)
    assert (root / "etc" / "motd").read_bytes() == b"private bits\n"
    _assert_credentials_in_place(reg)


# ----- push ----------------------------------------------------------------

def test_a_pushed_image_is_installed_back_with_credentials(
        registry, builders, monkeypatch):
    arch = get_device_cpu_arch()
    ref = f"{registry.host()}/team/app:1"
    _built(builders, ref, arch, "team/app")
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    command_push(_push_args(ref))
    assert registry.token_scopes() == ["repository:team/app:pull,push"]
    assert ("team/app", "1") in registry.manifests
    _assert_credentials_in_place(registry)

    # Nothing left locally, so the install has to come from the registry:
    # what the push wrote is what the pull reads.
    _empty_caches()
    command_install(SimpleNamespace(image_ref=ref,
                                    custom_container_name="back",
                                    override_arch=None))
    with open(os.path.join(container_rootfs("back"), "etc", "motd"),
              "rb") as fh:
        assert fh.read() == b"private bits\n"
    assert registry.token_scopes() == ["repository:team/app:pull,push",
                                       "repository:team/app:pull"]
    _assert_credentials_in_place(registry)


def test_a_push_without_credentials_is_refused(registry, builders, capsys):
    # Anonymous pull is allowed here, so the push gets as far as the
    # upload before the registry says no.
    registry.public.add("team/app")
    ref = f"{registry.host()}/team/app:1"
    _built(builders, ref, get_device_cpu_arch(), "team/app")
    with pytest.raises(SystemExit) as exc:
        command_push(_push_args(ref))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Push denied" in err
    assert "Set PD_DOCKER_AUTH" in err
    assert any(s.method == "POST" for s in registry.seen("registry"))
    assert registry.blobs == {}
    assert registry.manifests == {}
    _assert_credentials_in_place(registry, password="")


def test_a_push_with_a_wrong_password_is_refused(
        registry, builders, monkeypatch, capsys):
    ref = f"{registry.host()}/team/app:1"
    _built(builders, ref, get_device_cpu_arch(), "team/app")
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:wrong")
    with pytest.raises(SystemExit):
        command_push(_push_args(ref))
    err = capsys.readouterr().err
    assert "Push denied" in err
    assert "HTTP 401" in err
    assert [s.path for s in registry.seen("registry")] == ["/v2/"]
    assert registry.manifests == {}


def test_an_upload_handed_to_another_origin_goes_without_the_token(
        registry, builders, monkeypatch):
    # The upload session's Location points at storage elsewhere; the
    # PUT goes there bare (storage would answer 400 to a credential).
    registry.upload_origin = "cdn"
    arch = get_device_cpu_arch()
    ref = f"{registry.host()}/team/app:1"
    _built(builders, ref, arch, "team/app")
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    push_image(ref, arch)
    assert [s.method for s in registry.seen("cdn")] == ["PUT", "PUT"]
    assert ("team/app", "1") in registry.manifests
    _assert_credentials_in_place(registry)


def test_a_docker_hub_push_authenticates_at_the_hub_token_service(
        registry, as_docker_hub, builders, monkeypatch):
    as_docker_hub(registry)
    arch = get_device_cpu_arch()
    _built(builders, "alice/app:1", arch, "alice/app")
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    result = push_image("alice/app:1", arch)
    assert result["registry"] == "docker.io"
    assert registry.token_scopes() == ["repository:alice/app:pull,push"]
    assert "service=registry.docker.io" in registry.seen("auth")[0].path
    assert "/v2/" not in [s.path for s in registry.seen("registry")]
    assert ("alice/app", "1") in registry.manifests
    _assert_credentials_in_place(registry)


# ----- registries that check passwords themselves --------------------------
#
# A registry whose /v2/ challenge is Basic has no token service: Docker
# sends the credentials to it, on every request. proot-distro used to
# take any challenge but Bearer for an open registry and send nothing at
# all, so PD_DOCKER_AUTH never reached the registry, and the refusal
# told the user to check credentials that had not been sent.

def test_a_basic_registry_gets_the_credentials_on_a_pull(
        basic_registry, monkeypatch, tmp_path):
    reg = basic_registry
    reg.add_image("team/private", "1", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    root = _rootfs(tmp_path)
    meta = pull_image_into(f"{reg.host()}/team/private:1", str(root),
                           "x86_64")
    assert (root / "etc" / "motd").read_bytes() == b"private bits\n"
    assert meta["image_config"]["config"]["Env"] == ["FROM_THE_IMAGE=1"]
    # Off to storage for config and layer -- without the password, which
    # storage would have refused with 400.
    assert len(reg.seen("cdn")) == 2
    _assert_credentials_in_place(reg)


def test_a_basic_registry_without_credentials_is_refused(
        basic_registry, tmp_path):
    reg = basic_registry
    reg.add_image("team/private", "1", FILES, CONFIG)
    with pytest.raises(RuntimeError) as exc:
        pull_image_into(f"{reg.host()}/team/private:1",
                        str(_rootfs(tmp_path)), "x86_64")
    assert "does not exist or is a private image" in str(exc.value)
    _assert_credentials_in_place(reg, password="")


def test_a_basic_registry_with_a_wrong_password_is_refused(
        basic_registry, monkeypatch, tmp_path):
    reg = basic_registry
    reg.add_image("team/private", "1", FILES, CONFIG)
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:wrong")
    with pytest.raises(RuntimeError) as exc:
        pull_image_into(f"{reg.host()}/team/private:1",
                        str(_rootfs(tmp_path)), "x86_64")
    assert "Access denied" in str(exc.value)
    assert "HTTP 401" in str(exc.value)
    _assert_credentials_in_place(reg, password=basic("alice", "wrong"))


def test_a_basic_registry_public_image_is_pulled_anonymously(
        basic_registry, tmp_path):
    reg = basic_registry
    reg.public.add("team/public")
    reg.add_image("team/public", "1", FILES, CONFIG)
    root = _rootfs(tmp_path)
    pull_image_into(f"{reg.host()}/team/public:1", str(root), "x86_64")
    assert (root / "etc" / "motd").read_bytes() == b"private bits\n"
    _assert_credentials_in_place(reg, password="")


def test_a_basic_registry_gets_the_credentials_on_a_push(
        basic_registry, builders, monkeypatch):
    reg = basic_registry
    arch = get_device_cpu_arch()
    ref = f"{reg.host()}/team/app:1"
    _built(builders, ref, arch, "team/app")
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    command_push(_push_args(ref))
    assert ("team/app", "1") in reg.manifests
    _assert_credentials_in_place(reg)

    _empty_caches()
    command_install(SimpleNamespace(image_ref=ref,
                                    custom_container_name="back",
                                    override_arch=None))
    with open(os.path.join(container_rootfs("back"), "etc", "motd"),
              "rb") as fh:
        assert fh.read() == b"private bits\n"
    _assert_credentials_in_place(reg)


def test_a_basic_registry_upload_handed_elsewhere_goes_without_the_password(
        basic_registry, builders, monkeypatch):
    reg = basic_registry
    reg.upload_origin = "cdn"
    arch = get_device_cpu_arch()
    ref = f"{reg.host()}/team/app:1"
    _built(builders, ref, arch, "team/app")
    monkeypatch.setenv("PD_DOCKER_AUTH", "alice:s3cret")
    push_image(ref, arch)
    assert [s.method for s in reg.seen("cdn")] == ["PUT", "PUT"]
    assert ("team/app", "1") in reg.manifests
    _assert_credentials_in_place(reg)


def test_a_basic_registry_push_without_credentials_is_refused(
        basic_registry, builders, capsys):
    reg = basic_registry
    reg.public.add("team/app")
    ref = f"{reg.host()}/team/app:1"
    _built(builders, ref, get_device_cpu_arch(), "team/app")
    with pytest.raises(SystemExit):
        command_push(_push_args(ref))
    assert "Push denied" in capsys.readouterr().err
    assert reg.blobs == {}
    assert reg.manifests == {}
    _assert_credentials_in_place(reg, password="")
