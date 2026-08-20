# Containment tests for where a build *writes*.
#
# COPY/ADD sources are covered by test_copy_step_traversal.py; this file is
# about the destination side, which runs on the host with nothing confining
# it: the rootfs a stage is assembled in is an ordinary directory, so a
# symlink the image ships aims any write that follows it wherever it likes.
# A legitimate image ships plenty of them (`/var/run -> /run` is in nearly
# every distro image), so the rule is not "refuse symlinks" but "resolve
# them the way the guest sees them" — every hop re-anchored at the rootfs.

import os
import stat
from types import SimpleNamespace

import pytest

from proot_distro.helpers.build_engine import copy_step, handlers
from proot_distro.helpers.build_engine.stage import Stage


@pytest.fixture
def engine(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    stage = Stage(index=0, name=None, rootfs_dir=str(rootfs),
                  target_arch_pd="x86_64")
    eng = SimpleNamespace(
        current=stage, tmp_root=str(tmp_root), user_build_args={},
        global_args={}, declared_global=set(),
    )
    return eng, rootfs, outside


def _workdir(engine, path):
    handlers.do_workdir(engine, {
        "name": "WORKDIR", "value": path, "exec_form": False,
        "flags": {}, "heredocs": [], "lineno": 1,
    })


# --- WORKDIR ---------------------------------------------------------------

def test_workdir_through_a_symlinked_component_stays_inside(engine):
    eng, rootfs, outside = engine
    os.symlink(str(outside), str(rootfs / "x"))

    _workdir(eng, "/x/sub")

    # Nothing was created on the host side of the link.
    assert os.listdir(str(outside)) == []
    # The cwd recorded in the image config is still what the Dockerfile said.
    assert eng.current.image_config["config"]["WorkingDir"] == "/x/sub"


def test_workdir_does_not_chmod_through_a_final_symlink(engine):
    eng, rootfs, outside = engine
    os.symlink(str(outside), str(rootfs / "x"))

    _workdir(eng, "/x")

    assert stat.S_IMODE(outside.stat().st_mode) == 0o700


def test_workdir_follows_an_in_image_symlink_clamped_to_the_rootfs(engine):
    # The ordinary case: /var/run -> /run must still work, and must land
    # inside the rootfs rather than on the host's /run.
    eng, rootfs, _outside = engine
    (rootfs / "run").mkdir()
    (rootfs / "var").mkdir()
    os.symlink("/run", str(rootfs / "var" / "run"))

    _workdir(eng, "/var/run/app")

    assert os.path.isdir(str(rootfs / "run" / "app"))
    assert eng.current.image_config["config"]["WorkingDir"] == "/var/run/app"
    # The layer names the directory where it really landed.
    assert eng.current.layers


def test_workdir_absolute_symlink_target_is_re_rooted(engine, tmp_path):
    # An absolute target is the guest's "/", not the host's.
    eng, rootfs, _outside = engine
    os.symlink("/", str(rootfs / "escape"))

    _workdir(eng, "/escape/etc/app")

    assert os.path.isdir(str(rootfs / "etc" / "app"))


def test_workdir_dotdot_still_clamps_at_the_image_root(engine):
    eng, rootfs, _outside = engine

    _workdir(eng, "/../../../x")

    assert eng.current.image_config["config"]["WorkingDir"] == "/x"
    assert os.path.isdir(str(rootfs / "x"))
    assert not os.path.exists(str(rootfs.parent / "x"))


def test_workdir_plain_path_is_created_and_chmodded(engine):
    eng, rootfs, _outside = engine

    _workdir(eng, "/app/sub")

    made = rootfs / "app" / "sub"
    assert os.path.isdir(str(made))
    assert stat.S_IMODE(made.stat().st_mode) == 0o755


# --- COPY/ADD materialisation ----------------------------------------------

def test_materialise_dir_over_a_symlink_does_not_chmod_the_target(tmp_path):
    # `_safe_resolve` covers the *parents* of every entry; the final
    # component is deliberately left alone so the entry itself is replaced
    # rather than written through. A directory entry has to drop a link
    # standing there for that to hold — os.makedirs(exist_ok=True) is happy
    # with a symlink to a directory and os.chmod() follows one.
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    (outside / "keep").write_text("KEEP")
    os.symlink(str(outside), str(rootfs / "etc"))

    copy_step._materialise_files(str(rootfs), {
        "etc": {"kind": "dir", "mode": 0o777, "uid": 0, "gid": 0, "mtime": 0},
    })

    assert stat.S_IMODE(outside.stat().st_mode) == 0o700
    # The link was replaced by a real directory, as the layer records it
    # and as the tar extractor would apply it.
    made = rootfs / "etc"
    assert made.is_dir() and not made.is_symlink()
    assert stat.S_IMODE(made.stat().st_mode) == 0o777
    # The host directory's contents did not become the image's.
    assert os.listdir(str(made)) == []
    assert (outside / "keep").read_text() == "KEEP"


def test_materialise_dir_keeps_an_existing_real_directory(tmp_path):
    rootfs = tmp_path / "rootfs"
    (rootfs / "etc").mkdir(parents=True)
    (rootfs / "etc" / "kept").write_text("x")

    copy_step._materialise_files(str(rootfs), {
        "etc": {"kind": "dir", "mode": 0o755, "uid": 0, "gid": 0, "mtime": 0},
    })

    assert (rootfs / "etc" / "kept").read_text() == "x"


def test_materialise_tar_dir_member_lands_inside_the_rootfs(tmp_path):
    # The whole shape of the reported case: an ADD'd archive carrying a
    # directory whose name the image already points outside.
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir(mode=0o700)
    os.symlink(str(outside), str(rootfs / "etc"))

    payload = tmp_path / "payload"
    payload.write_bytes(b"pwned\n")
    copy_step._materialise_files(str(rootfs), {
        "etc": {"kind": "dir", "mode": 0o755, "uid": 0, "gid": 0, "mtime": 0},
        "etc/passwd": {"kind": "file", "src": str(payload),
                       "mode": 0o644, "uid": 0, "gid": 0, "mtime": 0},
    })

    assert (rootfs / "etc" / "passwd").read_bytes() == b"pwned\n"
    assert not (outside / "passwd").exists()
