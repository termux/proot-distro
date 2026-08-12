# Integration tests for zstd archives: backup/restore round trips,
# installs from a .tar.zst rootfs, and — on any interpreter — the
# refusals shown when the format cannot be handled.

import io
import os
import shutil
import tarfile
from types import SimpleNamespace

import pytest

from proot_distro import compress
from proot_distro.commands import backup as backup_mod
from proot_distro.commands import install_local as install_local_mod
from proot_distro.commands import restore as restore_mod
from proot_distro.commands.backup import command_backup
from proot_distro.commands.restore import command_restore
from proot_distro.helpers import tar_extract as tar_extract_mod
from proot_distro.helpers.docker import pull as pull_mod
from proot_distro.paths import container_dir, container_rootfs


needs_zstd = pytest.mark.skipif(
    not compress.ZSTD_AVAILABLE,
    reason="interpreter has no zstd support (needs Python 3.14 + libzstd)",
)


def _backup(name, out=None, compression=None):
    command_backup(SimpleNamespace(
        container_name=name, output=None if out is None else str(out),
        compression=compression, verbose=False,
    ))


def _fake_zstd_file(path):
    """Write a file that sniffs as zstd without needing libzstd to make it."""
    with open(path, "wb") as fh:
        fh.write(compress.ZSTD_MAGIC + b"\x00" * 64)
    return str(path)


# ---------------------------------------------------------------------------
# Round trips (need a zstd-capable interpreter)
# ---------------------------------------------------------------------------

@needs_zstd
@pytest.mark.parametrize("out_name,arg", [
    ("bk.tar.zst", None),       # inferred from the extension
    ("bk.archive", "zstd"),     # forced by --compress
])
def test_backup_restore_roundtrip_zstd(tmp_path, builders, out_name, arg):
    builders.make_container("zbox")
    root = container_rootfs("zbox")
    with open(os.path.join(root, "etc", "hostname"), "wb") as fh:
        fh.write(b"guest\n")
    os.symlink("etc/hostname", os.path.join(root, "hnlink"))
    expected = builders.tree_snapshot(root)

    out = tmp_path / out_name
    _backup("zbox", out, compression=arg)
    assert out.read_bytes()[:4] == compress.ZSTD_MAGIC

    shutil.rmtree(container_dir("zbox"))
    command_restore(SimpleNamespace(archive=str(out), verbose=False))
    assert builders.tree_snapshot(container_rootfs("zbox")) == expected


@needs_zstd
def test_backup_to_stdout_is_zstd_and_restores(tmp_path, builders,
                                               monkeypatch):
    # The piped form is the one tarfile's own w|zst mode would leave at
    # the default compression level; it must produce the same archive as
    # writing to a file does.
    builders.make_container("pbox")

    captured = io.BytesIO()

    class _Pipe:
        buffer = captured

        def isatty(self):
            return False

    monkeypatch.setattr(backup_mod.sys, "stdout", _Pipe())
    _backup("pbox", None, compression="zstd")
    piped = captured.getvalue()
    assert piped[:4] == compress.ZSTD_MAGIC

    out = tmp_path / "same.tar.zst"
    _backup("pbox", out, compression="zstd")
    assert out.read_bytes() == piped

    shutil.rmtree(container_dir("pbox"))
    command_restore(SimpleNamespace(archive=str(out), verbose=False))
    assert os.path.isdir(container_rootfs("pbox"))


@needs_zstd
def test_restore_from_zstd_stdin(tmp_path, builders, monkeypatch):
    # stdin has no filename to go by, so the magic-byte table is what
    # picks the decompressor.
    builders.make_container("sbox")
    expected = builders.tree_snapshot(container_rootfs("sbox"))
    out = tmp_path / "s.tar.zst"
    _backup("sbox", out, compression="zstd")
    shutil.rmtree(container_dir("sbox"))

    class _Stdin:
        buffer = io.BufferedReader(io.BytesIO(out.read_bytes()))

        def isatty(self):
            return False

    monkeypatch.setattr(restore_mod.sys, "stdin", _Stdin())
    command_restore(SimpleNamespace(archive=None, verbose=False))
    assert builders.tree_snapshot(container_rootfs("sbox")) == expected


@needs_zstd
def test_install_from_zstd_rootfs_tarball(tmp_path, builders):
    arc = tmp_path / "rootfs.tar.zst"
    builders.make_tar(str(arc), builders.rootfs_members(), compression="zst")
    root = tmp_path / "dest"
    root.mkdir()

    assert install_local_mod.install_from_local_file(
        str(arc), str(root), "x86_64"
    ) is None
    assert open(os.path.join(str(root), "etc", "hostname"), "rb").read() \
        == b"guest\n"


@needs_zstd
def test_zstd_layer_extracts(tmp_path, builders):
    # A zstd-compressed layer blob rides on tarfile's r|* auto-detect,
    # the same path a gzip layer takes.
    blob = tmp_path / "layer.tar.zst"
    builders.make_tar(str(blob), [
        {"name": "etc/", "type": "dir"},
        {"name": "etc/os-release", "type": "file", "data": b"ID=z\n"},
    ], compression="zst")
    root = tmp_path / "rootfs"
    root.mkdir()
    tar_extract_mod.extract_tar_to_rootfs(str(blob), str(root))
    assert open(os.path.join(str(root), "etc", "os-release"), "rb").read() \
        == b"ID=z\n"


# ---------------------------------------------------------------------------
# Refusals when the interpreter cannot do zstd (run everywhere)
# ---------------------------------------------------------------------------

def test_backup_refuses_zstd_when_unavailable(tmp_path, builders,
                                              monkeypatch, capsys):
    builders.make_container("nobox")
    monkeypatch.setattr(backup_mod, "ZSTD_AVAILABLE", False)

    for kwargs in ({"out": tmp_path / "a.tar.zst"},
                   {"out": tmp_path / "b.tar", "compression": "zstd"}):
        with pytest.raises(SystemExit) as exc:
            _backup("nobox", **kwargs)
        assert exc.value.code == 1
        err = capsys.readouterr().err
        assert "zstd" in err
        assert "3.14" in err or "libzstd" in err
        # Nothing half-written left behind.
        assert not os.path.exists(str(kwargs["out"]))


def test_restore_names_zstd_instead_of_corruption(tmp_path, monkeypatch,
                                                  capsys):
    monkeypatch.setattr(restore_mod, "ZSTD_AVAILABLE", False)
    arc = _fake_zstd_file(tmp_path / "backup.tar.zst")

    with pytest.raises(SystemExit) as exc:
        command_restore(SimpleNamespace(archive=arc, verbose=False))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "zstd" in err
    # The old failure mode: tarfile reporting a truncated/corrupt archive.
    assert "corrupted" not in err


def test_restore_stdin_names_zstd_instead_of_corruption(monkeypatch, capsys):
    monkeypatch.setattr(restore_mod, "ZSTD_AVAILABLE", False)

    class _Stdin:
        buffer = io.BufferedReader(
            io.BytesIO(compress.ZSTD_MAGIC + b"\x00" * 64)
        )

        def isatty(self):
            return False

    monkeypatch.setattr(restore_mod.sys, "stdin", _Stdin())
    with pytest.raises(SystemExit) as exc:
        command_restore(SimpleNamespace(archive=None, verbose=False))
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "zstd" in err and "corrupted" not in err


def test_local_install_names_zstd_instead_of_corruption(tmp_path, monkeypatch):
    monkeypatch.setattr(compress, "ZSTD_AVAILABLE", False)
    arc = _fake_zstd_file(tmp_path / "rootfs.tar.zst")
    root = tmp_path / "dest"
    root.mkdir()

    with pytest.raises(RuntimeError) as exc:
        install_local_mod.install_from_local_file(arc, str(root), "x86_64")
    assert "zstd" in str(exc.value)
    with pytest.raises(RuntimeError) as exc:
        tar_extract_mod.extract_tar_to_rootfs(arc, str(root))
    assert "zstd" in str(exc.value)


def test_pull_gate_follows_the_probe(tmp_path, builders, monkeypatch):
    from proot_distro.helpers.docker.cache import save_manifest_cache

    digest, size, diff_id = builders.seed_cached_layer(
        [{"name": "etc/x", "type": "file", "data": b"z"}]
    )
    save_manifest_cache("x:zmedia", "x86_64", {
        "schemaVersion": 2,
        "layers": [{
            "digest": digest, "size": size,
            "mediaType": "application/vnd.oci.image.layer.v1.tar+zstd",
        }],
    }, "library/x", {"rootfs": {"diff_ids": [diff_id]}})
    root = tmp_path / "rootfs"
    root.mkdir()

    monkeypatch.setattr(pull_mod, "ZSTD_AVAILABLE", False)
    with pytest.raises(RuntimeError) as exc:
        pull_mod.pull_image("x:zmedia", str(root), "x86_64")
    assert "zstd" in str(exc.value)

    # With support present the mediaType is no longer a reason to stop:
    # the blob itself decides, via auto-detect.
    monkeypatch.setattr(pull_mod, "ZSTD_AVAILABLE", True)
    pull_mod.pull_image("x:zmedia", str(root), "x86_64")
    assert os.path.isfile(os.path.join(str(root), "etc", "x"))


def test_unsupported_message_names_the_cause(monkeypatch):
    text = compress.unsupported_msg("archive 'x.tar.zst'")
    assert text.startswith("archive 'x.tar.zst' uses zstd compression")
    assert "3.14" in text or "libzstd" in text


def test_uncompressed_and_gzip_paths_unchanged(tmp_path, builders):
    # The zstd branch must not have moved the ground under the formats
    # that were already supported.
    builders.make_container("plain")
    expected = builders.tree_snapshot(container_rootfs("plain"))
    for name in ("p.tar", "p.tar.gz", "p.tar.xz"):
        out = tmp_path / name
        _backup("plain", out)
        with tarfile.open(str(out), "r:*") as tf:
            assert any(m.name.endswith("rootfs") or "/rootfs/" in m.name
                       for m in tf.getmembers())
    shutil.rmtree(container_dir("plain"))
    command_restore(SimpleNamespace(
        archive=str(tmp_path / "p.tar.gz"), verbose=False,
    ))
    assert builders.tree_snapshot(container_rootfs("plain")) == expected
