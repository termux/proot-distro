# A reference pinned by digest (`debian:12@sha256:…`) names content, and
# the pull has to deliver exactly that content or nothing.
#
# Such a reference used to be misparsed -- `library/ubuntu@sha256` as the
# repository, the hex as the tag -- so it could not be pulled at all. Now
# it is pulled by its digest, every document under it is checked against
# the digest that named it (the pin, the arch's manifest out of an index,
# the config, the layers), and the manifest-cache entry keeps the raw
# documents, so that a later hit is checked all over again. The entry's
# own `manifest` and `image_config` are never taken at their word for a
# pin: on Termux the cache is a directory a guest can write.
#
# The registry is faked at the opener and the token exchange; no network.

import base64
import hashlib
import json
import os
import urllib.error
from types import SimpleNamespace

import pytest

from _builders import pull_image_into
from proot_distro.constants import MANIFEST_CACHE_DIR
from proot_distro.helpers import download
from proot_distro.helpers.docker import pull as pull_mod
from proot_distro.helpers.docker.cache import (
    load_manifest_cache, load_manifest_documents, manifest_cache_name,
)
from proot_distro.helpers.docker.media import (
    OCI_CONFIG_MEDIA, OCI_INDEX_MEDIA, OCI_LAYER_MEDIA, OCI_MANIFEST_MEDIA,
)

BENIGN = [{"name": "etc/", "type": "dir"},
          {"name": "etc/os-release", "type": "file", "data": b"ID=real\n"}]
EVIL = [{"name": "etc/", "type": "dir"},
        {"name": "etc/PWNED", "type": "file", "data": b"attacker\n"}]


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(download.time, "sleep", lambda *a, **k: None)


def _digest(body):
    return "sha256:" + hashlib.sha256(body).hexdigest()


def _json(obj):
    return json.dumps(obj, sort_keys=True).encode()


class _Registry:
    """Serves manifests and blobs by digest, recording every request."""

    def __init__(self, builders, members=BENIGN, env="PIN=1"):
        layer, size, _diff = builders.seed_cached_layer(members)
        self.config = _json({"architecture": "amd64",
                             "config": {"Env": [env]}})
        self.manifest = _json({
            "schemaVersion": 2, "mediaType": OCI_MANIFEST_MEDIA,
            "config": {"digest": _digest(self.config),
                       "size": len(self.config),
                       "mediaType": OCI_CONFIG_MEDIA},
            "layers": [{"digest": layer, "size": size,
                        "mediaType": OCI_LAYER_MEDIA}],
        })
        self.index = _json({
            "schemaVersion": 2, "mediaType": OCI_INDEX_MEDIA,
            "manifests": [{"digest": _digest(self.manifest),
                           "mediaType": OCI_MANIFEST_MEDIA,
                           "platform": {"os": "linux",
                                        "architecture": "amd64"}}],
        })
        self.layer = layer
        self.docs = {_digest(d): d
                     for d in (self.config, self.manifest, self.index)}
        self.requests = []
        self.down = False

    def install(self, monkeypatch):
        monkeypatch.setattr(
            pull_mod, "get_auth_token",
            lambda repo, registry, insecure=False: ("TKN", "https://r"))
        monkeypatch.setattr(
            pull_mod, "opener",
            lambda insecure=False: SimpleNamespace(open=self._open))
        return self

    def _open(self, req, timeout=None):
        url = req.full_url
        self.requests.append(url)
        if self.down:
            raise urllib.error.URLError("offline")
        ref = url.rsplit("/", 1)[-1]
        body = self.docs.get(ref)
        if body is None:
            raise urllib.error.HTTPError(url, 404, "not found", {}, None)
        return _Resp(body)


class _Resp:
    def __init__(self, body):
        self._body = body
        # The header says nothing the pin covers; a pinned pull reads the
        # media type off the document instead.
        self.headers = {"Content-Type": "application/octet-stream"}

    def read(self, n=-1):
        body, self._body = self._body, b""
        return body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def _pull(tmp_path, ref, name="rootfs"):
    root = tmp_path / name
    root.mkdir()
    meta = pull_image_into(ref, str(root), "x86_64")
    return root, meta


def _entry_path(ref):
    return os.path.join(MANIFEST_CACHE_DIR, manifest_cache_name(ref, "x86_64"))


def _rewrite_entry(ref, change):
    path = _entry_path(ref)
    with open(path) as fh:
        payload = json.load(fh)
    change(payload)
    with open(path, "w") as fh:
        json.dump(payload, fh)


# ---------------------------------------------------------------------------
# pulling by digest
# ---------------------------------------------------------------------------

def test_a_pin_on_an_index_is_pulled_by_digest(tmp_path, builders,
                                              monkeypatch):
    reg = _Registry(builders).install(monkeypatch)
    pin = _digest(reg.index)
    root, meta = _pull(tmp_path, f"img@{pin}")

    assert (root / "etc" / "os-release").read_bytes() == b"ID=real\n"
    assert meta["image_config"]["config"]["Env"] == ["PIN=1"]
    assert reg.requests == [
        f"https://r/v2/library/img/manifests/{pin}",
        f"https://r/v2/library/img/manifests/{_digest(reg.manifest)}",
        f"https://r/v2/library/img/blobs/{_digest(reg.config)}",
    ]
    # Every document the chain passed through is kept, byte for byte.
    assert load_manifest_documents(f"img@{pin}", "x86_64") == reg.docs


def test_a_pin_on_a_manifest_is_pulled_by_digest(tmp_path, builders,
                                                monkeypatch):
    reg = _Registry(builders).install(monkeypatch)
    pin = _digest(reg.manifest)
    root, meta = _pull(tmp_path, f"img@{pin}")
    assert (root / "etc" / "os-release").read_bytes() == b"ID=real\n"
    assert set(load_manifest_documents(f"img@{pin}", "x86_64")) == {
        pin, _digest(reg.config)}


def test_the_tag_beside_a_pin_is_not_consulted(tmp_path, builders,
                                               monkeypatch):
    reg = _Registry(builders).install(monkeypatch)
    pin = _digest(reg.index)
    _pull(tmp_path, f"img:whatever@{pin}")
    assert not any("whatever" in url for url in reg.requests)
    # Both spellings are one image, and one cache entry.
    assert manifest_cache_name(f"img:whatever@{pin}", "x86_64") == \
        manifest_cache_name(f"img@{pin}", "x86_64")


def test_a_registry_answering_something_else_for_the_pin_is_refused(
        tmp_path, builders, monkeypatch):
    reg = _Registry(builders).install(monkeypatch)
    pin = _digest(reg.index)
    reg.docs[pin] = reg.index.replace(b"amd64", b"arm64")
    with pytest.raises(RuntimeError, match="does not match its digest"):
        _pull(tmp_path, f"img@{pin}")
    assert load_manifest_cache(f"img@{pin}", "x86_64")[0] is None


def test_a_pinned_config_that_cannot_be_fetched_is_fatal(tmp_path, builders,
                                                        monkeypatch):
    # A tag degrades to an empty config when the blob is unreachable; the
    # image a pin names has the config it names, or it is not that image.
    reg = _Registry(builders).install(monkeypatch)
    pin = _digest(reg.index)
    del reg.docs[_digest(reg.config)]
    with pytest.raises(RuntimeError, match="Image not found"):
        _pull(tmp_path, f"img@{pin}")
    assert load_manifest_cache(f"img@{pin}", "x86_64")[0] is None


# ---------------------------------------------------------------------------
# a cache hit is checked against the pin, not believed
# ---------------------------------------------------------------------------

def test_a_pinned_image_installs_offline_the_second_time(tmp_path, builders,
                                                        monkeypatch):
    reg = _Registry(builders).install(monkeypatch)
    ref = f"img@{_digest(reg.index)}"
    _pull(tmp_path, ref, "first")

    reg.requests.clear()
    reg.down = True
    root, meta = _pull(tmp_path, ref, "second")
    assert reg.requests == []
    assert (root / "etc" / "os-release").read_bytes() == b"ID=real\n"
    assert meta["image_config"]["config"]["Env"] == ["PIN=1"]


def test_a_rewritten_entry_does_not_decide_what_a_pin_installs(
        tmp_path, builders, monkeypatch):
    # The entry's own fields are a guest's to rewrite on Termux. A tag has
    # nothing to hold them to; a pin does, and they are not even read.
    reg = _Registry(builders).install(monkeypatch)
    ref = f"img@{_digest(reg.index)}"
    _pull(tmp_path, ref, "first")

    evil_layer, size, _ = builders.seed_cached_layer(EVIL)

    def plant(payload):
        payload["manifest"]["layers"] = [
            {"digest": evil_layer, "size": size,
             "mediaType": OCI_LAYER_MEDIA}]
        payload["image_config"] = {"config": {"Env": ["EVIL=1"]}}

    _rewrite_entry(ref, plant)
    reg.down = True
    root, meta = _pull(tmp_path, ref, "second")
    assert (root / "etc" / "os-release").read_bytes() == b"ID=real\n"
    assert not (root / "etc" / "PWNED").exists()
    assert meta["image_config"]["config"]["Env"] == ["PIN=1"]


@pytest.mark.parametrize("which", ["index", "manifest", "config"])
def test_a_tampered_document_is_refetched_not_used(tmp_path, builders,
                                                   monkeypatch, capsys,
                                                   which):
    reg = _Registry(builders).install(monkeypatch)
    ref = f"img@{_digest(reg.index)}"
    _pull(tmp_path, ref, "first")

    evil_config = _json({"architecture": "amd64",
                         "config": {"Env": ["EVIL=1"]}})
    target = _digest(getattr(reg, which))

    def tamper(payload):
        payload["documents"][target] = base64.b64encode(
            evil_config).decode()

    _rewrite_entry(ref, tamper)
    reg.requests.clear()
    root, meta = _pull(tmp_path, ref, "second")
    assert "does not verify against its digest" in capsys.readouterr().err
    assert reg.requests, "the tampered entry was used instead of refetched"
    assert meta["image_config"]["config"]["Env"] == ["PIN=1"]

    # Offline, the same tampering is a failure, never the tampered image.
    _rewrite_entry(ref, tamper)
    reg.down = True
    with pytest.raises(RuntimeError, match="Network error"):
        _pull(tmp_path, ref, "third")


def test_an_entry_without_documents_is_a_miss(tmp_path, builders,
                                              monkeypatch):
    # What a pinned entry looks like when someone other than a pull wrote
    # it: a manifest and a config, and nothing to check them against.
    reg = _Registry(builders).install(monkeypatch)
    ref = f"img@{_digest(reg.index)}"
    _pull(tmp_path, ref, "first")
    _rewrite_entry(ref, lambda payload: payload.pop("documents"))

    reg.down = True
    with pytest.raises(RuntimeError, match="Network error"):
        _pull(tmp_path, ref, "second")


def test_a_tag_entry_never_answers_for_a_pin(tmp_path, builders,
                                             monkeypatch):
    from proot_distro.helpers.docker.cache import save_manifest_cache

    reg = _Registry(builders).install(monkeypatch)
    evil_layer, size, _ = builders.seed_cached_layer(EVIL)
    save_manifest_cache(
        "img:latest", "x86_64",
        {"schemaVersion": 2,
         "layers": [{"digest": evil_layer, "size": size,
                     "mediaType": OCI_LAYER_MEDIA}]},
        "library/img", {})
    root, _meta = _pull(tmp_path, f"img@{_digest(reg.index)}")
    assert not (root / "etc" / "PWNED").exists()
    assert (root / "etc" / "os-release").read_bytes() == b"ID=real\n"


def test_pull_image_refuses_a_reference_the_grammar_does_not_accept(
        tmp_path, monkeypatch):
    def no_network(*_a, **_k):
        raise AssertionError("a refused reference reached the registry")

    monkeypatch.setattr(pull_mod, "get_auth_token", no_network)
    for ref in ("Ubuntu", "ubuntu#frag", "ubuntu@sha512:" + "a" * 128):
        with pytest.raises(RuntimeError):
            _pull(tmp_path, ref, name=f"r{len(ref)}")
