# Integration tests: every command an image reference comes into holds it
# to Docker's reference grammar, and does so before acting on it.
#
# `build -t` used to check the last path component alone, so `../../x:1`
# was accepted (with `..` as its registry) while `localhost:5000/app` was
# refused; `install` and `push` checked nothing, and an image reference was
# split rather than parsed wherever it went.

import hashlib
import os
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands import install as install_mod
from proot_distro.commands.build import command_build
from proot_distro.commands.install import (
    _resolve_install_name, command_install,
)
from proot_distro.commands.list import command_list
from proot_distro.commands.push import command_push
from proot_distro.commands.remove import command_remove
from proot_distro.helpers.docker.cache import (
    iter_cached_images, load_manifest_cache, save_manifest_cache,
)
from proot_distro.helpers.docker.media import OCI_LAYER_MEDIA, canonical_json
from proot_distro.locking import BuildLock
from proot_distro.paths import container_dir

_PIN = "sha256:" + "0123456789abcdef" * 4


def _build_args(ctx, tags, **over):
    base = dict(
        path=str(ctx), dockerfile=None, tags=tags, build_args=[],
        override_arch=None, target_stage=None, emulator=None, outputs=[],
        install_as=None, no_cache=False, verbose=False, quiet=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def _context(tmp_path, name="ctx"):
    ctx = tmp_path / name
    ctx.mkdir()
    (ctx / "f").write_text("x\n")
    (ctx / "Dockerfile").write_text("FROM scratch\nCOPY f /f\n")
    return ctx


# ----- build -t ------------------------------------------------------------

@pytest.mark.parametrize("tag,cached_as", [
    ("localhost:5000/app", "localhost:5000/app:latest"),
    ("[::1]:5000/app", "[::1]:5000/app:latest"),
    ("localhost/app:1", "localhost/app:1"),
])
def test_build_accepts_a_registry_with_a_port_or_localhost(tmp_path, tag,
                                                           cached_as):
    command_build(_build_args(_context(tmp_path), [tag]))
    manifest, _repo, _cfg = load_manifest_cache(
        cached_as, get_device_cpu_arch())
    assert manifest is not None


@pytest.mark.parametrize("tag,reason", [
    ("../../x:1", "registry host is not valid"),
    ("MyApp", "must be lowercase"),
    ("a//b", "invalid reference format"),
    ("app:", "invalid reference format"),
    (f"app@{_PIN}", "use a tag"),
])
def test_build_refuses_a_tag_that_is_no_reference(tmp_path, capsys, tag,
                                                  reason):
    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(_context(tmp_path), [tag]))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"tag '{tag}' is not valid" in err
    assert reason in err
    assert iter_cached_images() == []


def test_build_quotes_a_refused_tag(tmp_path, capsys):
    with pytest.raises(SystemExit):
        command_build(_build_args(_context(tmp_path), ["x\x1b[2Jy"]))
    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "x\\e[2Jy" in err


def test_build_derives_a_valid_tag_from_an_awkward_directory(tmp_path):
    # `app_` used to become the tag `app_:latest`, which no registry takes.
    command_build(_build_args(_context(tmp_path, "app_"), []))
    assert load_manifest_cache("app:latest", get_device_cpu_arch())[0]


def _no_pull(monkeypatch):
    # A reference the grammar refuses must never reach a registry; were
    # one to get through, this fails the test instead of going online.
    from proot_distro.helpers.build_engine import copy_step, engine

    def pull(*_a, **_k):
        raise AssertionError("a refused reference was pulled")

    monkeypatch.setattr(engine, "pull_image", pull)
    monkeypatch.setattr(copy_step, "pull_image", pull)


def test_build_from_a_reference_the_grammar_refuses(tmp_path, capsys,
                                                    monkeypatch):
    _no_pull(monkeypatch)
    ctx = _context(tmp_path)
    (ctx / "Dockerfile").write_text("FROM ubuntu#x\n")
    with pytest.raises(SystemExit):
        command_build(_build_args(ctx, ["t:1"]))
    assert ("Build failed: FROM ubuntu#x: invalid reference format"
            in capsys.readouterr().err)


def test_copy_from_a_reference_the_grammar_refuses(tmp_path, capsys,
                                                   monkeypatch):
    _no_pull(monkeypatch)
    ctx = _context(tmp_path)
    (ctx / "Dockerfile").write_text("FROM scratch\nCOPY --from=Ubuntu /a /a\n")
    with pytest.raises(SystemExit):
        command_build(_build_args(ctx, ["t:1"]))
    err = capsys.readouterr().err
    assert "COPY --from=Ubuntu: invalid reference format" in err


# ----- push ----------------------------------------------------------------

def _push(ref):
    command_push(SimpleNamespace(image_ref=ref, override_arch=None,
                                 quiet=True, allow_insecure=False))


@pytest.mark.parametrize("ref,reason", [
    (f"app@{_PIN}", "use a tag"),
    ("App:1", "must be lowercase"),
    ("ubuntu?x=1", "invalid reference format"),
])
def test_push_refuses_a_reference_it_cannot_push(capsys, ref, reason):
    with pytest.raises(SystemExit) as exc:
        _push(ref)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert f"cannot push '{ref}'" in err
    assert reason in err
    # Refused as what it is, not reported as a missing build.
    assert "No image found" not in err


# ----- install -------------------------------------------------------------

def _install(ref, name=None):
    command_install(SimpleNamespace(
        image_ref=ref, custom_container_name=name, override_arch=None))


@pytest.mark.parametrize("ref", ["Ubuntu", "ubuntu:", "ubuntu#frag"])
def test_install_refuses_a_reference_before_touching_anything(
        monkeypatch, capsys, ref):
    def pull(*_a, **_k):
        raise AssertionError("a refused reference was pulled")

    monkeypatch.setattr(install_mod, "pull_image", pull)
    with pytest.raises(SystemExit) as exc:
        _install(ref, name="box")
    assert exc.value.code == 1
    assert f"cannot install '{ref}'" in capsys.readouterr().err
    assert not os.path.lexists(container_dir("box"))


@pytest.mark.parametrize("ref,name", [
    (f"ubuntu@{_PIN}", "ubuntu"),
    (f"debian:12@{_PIN}", "debian"),
    (f"ghcr.io/foo/bar@{_PIN}", "bar"),
    ("localhost/app", "app"),
])
def test_install_names_a_pinned_container_after_its_repository(ref, name):
    # It used to be "cannot derive a valid container name": the alias
    # was taken from `ubuntu@sha256`.
    assert _resolve_install_name(ref, None, None, None) == name


def test_install_hands_a_pin_to_the_pull(monkeypatch, capsys):
    seen = []

    def pull(image_ref, rootfs_fd, arch, insecure=False):
        seen.append(image_ref)
        raise RuntimeError("stop here")

    monkeypatch.setattr(install_mod, "pull_image", pull)
    with pytest.raises(SystemExit):
        _install(f"debian:12@{_PIN}")
    assert seen == [f"debian:12@{_PIN}"]
    assert not os.path.lexists(container_dir("debian"))


# ----- one image, one lock -------------------------------------------------

def test_every_spelling_of_one_image_takes_the_same_build_lock():
    arch = "x86_64"
    first = BuildLock("myapp", arch, command="build")
    assert first.acquire()
    try:
        for spelling in ("myapp:latest", "docker.io/library/myapp",
                         "index.docker.io/library/myapp:latest"):
            other = BuildLock(spelling, arch, command="push")
            assert other.lock_path == first.lock_path
    finally:
        first.release()
    assert BuildLock("myapp:2", arch).lock_path != first.lock_path


# ----- list / remove --image for a pinned image ----------------------------

def _seed(builders, ref, arch="x86_64"):
    digest, size, _diff = builders.seed_cached_layer(
        [{"name": "etc/pinned", "type": "file", "data": b"p"}])
    config = {"architecture": "amd64", "os": "linux", "config": {}}
    config_bytes = canonical_json(config)
    manifest = {
        "schemaVersion": 2,
        "config": {"digest": "sha256:" + hashlib.sha256(config_bytes)
                   .hexdigest(), "size": len(config_bytes)},
        "layers": [{"digest": digest, "size": size,
                    "mediaType": OCI_LAYER_MEDIA}],
    }
    save_manifest_cache(ref, arch, manifest, "library/img", config)


def test_a_pinned_image_is_listed_and_removed_by_its_reference(builders,
                                                               capsys):
    ref = f"img:1@{_PIN}"
    _seed(builders, ref)
    command_list(SimpleNamespace(image=True, quiet=True))
    assert capsys.readouterr().out.split() == [ref]

    # Any spelling of the pin finds it: the tag is not part of its key.
    command_remove(SimpleNamespace(target=f"docker.io/library/img@{_PIN}",
                                   image=True, verbose=False,
                                   override_arch=None))
    assert iter_cached_images() == []
