# End-to-end build pipeline test that needs neither proot nor the network:
# a `FROM scratch` Dockerfile with COPY/ENV/WORKDIR/CMD is built, written to
# an OCI archive, and installed as a container (offline, via the cache).

import io
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


def _open_fds():
    return set(os.listdir("/proc/self/fd"))


def test_build_to_cache_archive_and_install(tmp_path, builders):
    ctx = _make_context(tmp_path)
    out_oci = tmp_path / "out.oci.tar"
    arch = get_device_cpu_arch()

    # A build now holds descriptors for the whole of its run -- the
    # scratch root, and two per stage -- so the release has to be exact
    # or a multi-stage Dockerfile would leak one pair per FROM.
    before = _open_fds()
    command_build(_build_args(
        ctx, outputs=[str(out_oci)], install_as="builtbox",
    ))
    assert _open_fds() - before == set()

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


def test_build_add_auto_extract_spools_to_disk(tmp_path, builders):
    # ADD used to hold every regular member of an auto-extracted archive in
    # memory at once, since one file_map covers a whole instruction. The
    # members are spooled to files now; what has to stay true is that the
    # tree and the layer still carry the content.
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    payload = ctx / "payload.tar"
    builders.make_tar(str(payload), [
        {"name": "etc", "type": "dir"},
        {"name": "etc/hostname", "type": "file", "data": b"built\n"},
        {"name": "etc/big", "type": "file", "data": b"Z" * (1 << 20)},
        {"name": "etc/alias", "type": "symlink", "linkname": "hostname"},
    ])
    (ctx / "Dockerfile").write_text(
        "FROM scratch\n"
        "ADD payload.tar /\n"
        'CMD ["/bin/sh"]\n'
    )

    command_build(_build_args(ctx, tags=["addimg:1"], install_as="addbox"))

    root = container_rootfs("addbox")
    with open(os.path.join(root, "etc", "hostname"), "rb") as fh:
        assert fh.read() == b"built\n"
    with open(os.path.join(root, "etc", "big"), "rb") as fh:
        assert fh.read() == b"Z" * (1 << 20)
    assert os.readlink(os.path.join(root, "etc", "alias")) == "hostname"

    # The spool directory lives in the build's tmp_root, which is removed
    # when the build ends — nothing of it is left next to the context.
    assert sorted(os.listdir(str(ctx))) == ["Dockerfile", "payload.tar"]


def test_build_add_url_spools_to_disk(tmp_path, builders, monkeypatch):
    import io
    from proot_distro.helpers.build_engine import copy_step

    body = b"N" * (1 << 20)

    class _Resp(io.BytesIO):
        # A real response declares its length, and ADD now holds the
        # answer to it: short of that is a truncated download.
        headers = {"Content-Length": str(len(body))}

        def __enter__(self):
            return self

        def __exit__(self, *a):
            self.close()
            return False

    class _Opener:
        def open(self, url):
            return _Resp(body)

    monkeypatch.setattr(copy_step.urllib.request, "build_opener",
                        lambda *a: _Opener())

    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(
        "FROM scratch\n"
        "ADD https://example.invalid/blob.bin /opt/blob.bin\n"
        'CMD ["/bin/sh"]\n'
    )

    command_build(_build_args(ctx, tags=["urlimg:1"], install_as="urlbox"))

    with open(os.path.join(container_rootfs("urlbox"), "opt", "blob.bin"),
              "rb") as fh:
        assert fh.read() == body


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


def test_build_copy_chmod_reaches_tree_and_layer(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "tool").write_text("#!/bin/sh\n")
    (ctx / "Dockerfile").write_text(
        "FROM scratch\n"
        "COPY --chmod=4755 tool /usr/bin/tool\n"
        'CMD ["/usr/bin/tool"]\n'
    )
    command_build(_build_args(ctx, tags=["chmodimg:1"], install_as="chmodbox"))

    root = container_rootfs("chmodbox")
    st = os.lstat(os.path.join(root, "usr", "bin", "tool"))
    assert st.st_mode & 0o7777 == 0o4755


@pytest.mark.parametrize("value", ["u+x", "0x755", "77777777777777", ""])
def test_build_refuses_a_bad_copy_chmod_with_a_message(tmp_path, capsys,
                                                       value):
    # `--chmod=u+x` used to build the image with the source's own mode
    # and say nothing; `--chmod=77777777777777` reached os.fchmod() and
    # ended the build in an OverflowError traceback.
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "tool").write_text("x")
    (ctx / "Dockerfile").write_text(
        "FROM scratch\n"
        f"COPY --chmod={value} tool /tool\n"
    )
    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(ctx, tags=["badchmod:1"], quiet=False))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "Build failed" in err
    assert "--chmod" in err and "line 2" in err


def test_build_reads_the_dockerfile_from_stdin(tmp_path, monkeypatch):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "tool").write_text("x")
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(
        b"FROM scratch\r\nCOPY tool /tool\r\n"   # CRLF: the parser's to fix
    )))
    command_build(_build_args(ctx, dockerfile="-", tags=["stdinimg:1"],
                              install_as="stdinbox"))
    assert os.path.isfile(os.path.join(container_rootfs("stdinbox"), "tool"))


def test_build_refuses_a_dockerfile_over_the_cap(tmp_path, monkeypatch,
                                                  capsys):
    # The file is read whole, and it is as often copied as written;
    # everything else this program parses out of a stranger's file goes
    # through a ceiling. The bound is on the bytes read, so a file (or a
    # pipe) has no size to lie about.
    from proot_distro.commands import build as build_mod
    monkeypatch.setattr(build_mod, "MAX_DOCKERFILE_BYTES", 64)
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(
        "FROM scratch\n" + "# " + "x" * 200 + "\n"
    )
    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(ctx, tags=["bigdf:1"], quiet=False))
    assert exc.value.code == 1
    assert "larger than 64 bytes" in capsys.readouterr().err

    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(
        b"FROM scratch\n# " + b"y" * 200 + b"\n"
    )))
    with pytest.raises(SystemExit) as exc:
        command_build(_build_args(ctx, dockerfile="-", tags=["bigdf:1"],
                                  quiet=False))
    assert exc.value.code == 1
    assert "larger than 64 bytes" in capsys.readouterr().err


def test_build_reads_a_dockerfile_at_the_cap(tmp_path, monkeypatch):
    from proot_distro.commands import build as build_mod
    text = "FROM scratch\n" + "#" * 50 + "\n"
    monkeypatch.setattr(build_mod, "MAX_DOCKERFILE_BYTES", len(text))
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "Dockerfile").write_text(text)
    command_build(_build_args(ctx, tags=["capdf:1"]))
