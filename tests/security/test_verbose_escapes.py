# Terminal-escape containment for the commands that print names back.
#
# A name inside a rootfs is whatever the guest called it, and a name inside
# a backup archive is whatever whoever built the archive called it. Both
# reach the terminal: `--verbose` logs every entry it touches, and a failure
# names the entry it failed on. A name holding ESC repaints the screen,
# hides the lines around it, or hands the emulator whatever sequence
# follows — so nothing below 0x20 may pass through literally.
#
# `copy` and `sync` were already held to this (test_copy_symlink_escape.py);
# these are the four commands that were not.

import io
import os
import shutil
import tarfile
from types import SimpleNamespace

import pytest

from proot_distro.commands.backup import command_backup
from proot_distro.commands.remove import command_remove
from proot_distro.commands.restore import command_restore
from proot_distro.paths import container_dir, container_rootfs

# Clear-screen, then red, then reset — plus the two whitespace controls that
# let a name forge a line of its own.
EVIL = "a\x1b[2J\x1b[31mPWNED\x1b[0m\tb\nc"
QUOTED = "a\\e[2J\\e[31mPWNED\\e[0m\\tb\\nc"


def _assert_no_raw_escapes(text):
    assert "\x1b" not in text
    for line in text.splitlines():
        assert "\r" not in line
    # The name is still shown, just spelled out.
    assert QUOTED in text


def _plant(container):
    rootfs = container_rootfs(container)
    os.makedirs(os.path.join(rootfs, "etc"), exist_ok=True)
    with open(os.path.join(rootfs, "etc", EVIL), "w") as fh:
        fh.write("x")
    return rootfs


def test_backup_verbose_quotes_a_guest_chosen_name(tmp_path, builders, capsys):
    builders.make_container("box")
    _plant("box")

    command_backup(SimpleNamespace(
        container_name="box", output=str(tmp_path / "b.tar"),
        compression=None, verbose=True,
    ))

    _assert_no_raw_escapes(capsys.readouterr().err)


def test_remove_verbose_quotes_a_guest_chosen_name(builders, capsys):
    builders.make_container("box")
    _plant("box")

    command_remove(SimpleNamespace(target="box", verbose=True))

    _assert_no_raw_escapes(capsys.readouterr().err)
    assert not os.path.exists(container_dir("box"))


def test_restore_verbose_quotes_an_archive_chosen_name(tmp_path, capsys):
    # The worst of the three: the name comes off an archive the user was
    # handed, so it is untrusted even before any container exists.
    arc = tmp_path / "evil.tar"
    with tarfile.open(str(arc), "w") as tf:
        info = tarfile.TarInfo("box/manifest.json")
        data = b"{}"
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        info = tarfile.TarInfo("box/rootfs/etc")
        info.type = tarfile.DIRTYPE
        info.mode = 0o755
        tf.addfile(info)
        info = tarfile.TarInfo(f"box/rootfs/etc/{EVIL}")
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))

    command_restore(SimpleNamespace(archive=str(arc), verbose=True))

    _assert_no_raw_escapes(capsys.readouterr().err)
    shutil.rmtree(container_dir("box"), ignore_errors=True)


def test_build_failure_quotes_a_name_from_an_added_archive(tmp_path, capsys):
    # A BuildError interpolates the offending name into its message raw,
    # and for an ADD'd archive that name is the archive's to choose.
    from proot_distro.commands.build import command_build

    ctx = tmp_path / "ctx"
    ctx.mkdir()
    payload = ctx / "pay.tar"
    with tarfile.open(str(payload), "w") as tf:
        info = tarfile.TarInfo(EVIL.replace("\n", "").replace("\t", ""))
        info.size = 1
        tf.addfile(info, io.BytesIO(b"x"))
    (ctx / "Dockerfile").write_text(
        "FROM scratch\n"
        "ADD pay.tar /\n"
    )

    # Make the materialiser fail on that entry so the name reaches the
    # error path: a directory standing where the member wants a file.
    from proot_distro.helpers.build_engine import copy_step
    real = copy_step._materialise_files

    def _boom(rootfs_dir, file_map, **kw):
        from proot_distro.helpers.build_engine.errors import BuildError
        arcname = next(iter(sorted(file_map)))
        raise BuildError(f"Failed to write '{arcname}' into rootfs: nope")

    copy_step._materialise_files = _boom
    try:
        with pytest.raises(SystemExit):
            command_build(SimpleNamespace(
                path=str(ctx), dockerfile=None, tags=["esc:1"],
                build_args=[], override_arch=None, target_stage=None,
                emulator=None, outputs=[], install_as=None,
                no_cache=True, verbose=False, quiet=False,
            ))
    finally:
        copy_step._materialise_files = real

    err = capsys.readouterr().err
    assert "\x1b" not in err
    assert "Build failed:" in err
