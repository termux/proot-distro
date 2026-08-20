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

from proot_distro.helpers.build_engine import handlers
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
