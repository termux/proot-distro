# End-to-end build pipeline test that needs neither proot nor the network:
# a `FROM scratch` Dockerfile with COPY/ENV/WORKDIR/CMD is built, written to
# an OCI archive, and installed as a container (offline, via the cache).

import json
import os
import tarfile
from types import SimpleNamespace

import pytest

from proot_distro.arch import get_device_cpu_arch
from proot_distro.commands.build import command_build
from proot_distro.helpers.docker.cache import load_manifest_cache
from proot_distro.paths import container_manifest, container_rootfs


def _make_context(tmp_path):
    ctx = tmp_path / "ctx"
    rootfs = ctx / "rootfs"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "etc" / "hostname").write_text("built\n")
    (rootfs / "etc" / "os-release").write_text("ID=built\n")
    (ctx / "Dockerfile").write_text(
        "FROM scratch\n"
        "COPY rootfs/ /\n"
        "ENV FOO=bar\n"
        "WORKDIR /app\n"
        'CMD ["/bin/sh"]\n'
    )
    return ctx


def _build_args(ctx, **over):
    base = dict(
        path=str(ctx), dockerfile=None, tags=["myimg:1"], build_args=[],
        override_arch=None, target_stage=None, emulator=None, outputs=[],
        install_as=None, no_cache=False, verbose=False, quiet=True,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_build_to_cache_archive_and_install(tmp_path, builders):
    ctx = _make_context(tmp_path)
    out_oci = tmp_path / "out.oci.tar"
    arch = get_device_cpu_arch()

    command_build(_build_args(
        ctx, outputs=[str(out_oci)], install_as="builtbox",
    ))

    # 1) Manifest cache written so `install myimg:1` works offline.
    manifest, repo, image_config = load_manifest_cache("myimg:1", arch)
    assert manifest is not None
    assert manifest["layers"], "build produced at least one layer"

    # 2) OCI archive is well-formed.
    assert out_oci.exists()
    with tarfile.open(str(out_oci)) as tf:
        names = tf.getnames()
        assert "oci-layout" in names
        assert "index.json" in names

    # 3) --install-as produced a real container with the built content+config.
    root = container_rootfs("builtbox")
    assert open(os.path.join(root, "etc", "hostname"), "rb").read() == b"built\n"
    with open(container_manifest("builtbox")) as fh:
        meta = json.load(fh)
    cfg = meta["image_config"]["config"]
    assert cfg["Cmd"] == ["/bin/sh"]
    assert cfg["WorkingDir"] == "/app"
    assert "FOO=bar" in cfg["Env"]
    # The WORKDIR directory exists in the installed rootfs.
    assert os.path.isdir(os.path.join(root, "app"))


def test_build_refuses_existing_output(tmp_path):
    ctx = _make_context(tmp_path)
    out = tmp_path / "exists.oci.tar"
    out.write_text("already here")
    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(ctx, outputs=[str(out)]))
    assert exc.value.code == 1


def test_build_refuses_existing_install_as(tmp_path, builders):
    ctx = _make_context(tmp_path)
    builders.make_container("taken")
    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(ctx, install_as="taken"))
    assert exc.value.code == 1


def test_build_syntax_error_reported(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text("NOTANINSTRUCTION foo\n")
    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(ctx))
    assert exc.value.code == 1
