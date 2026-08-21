# Containment tests for the stage rootfs a build works against.
#
# The build kept `<scratch>/stage-N/rootfs` as a *path* and re-resolved it
# for every snapshot, every cached-layer apply, every layer packed, every
# COPY/ADD and every RUN. The scratch root is 0700, but that is only the
# invoking user's own permission, and a process a previous RUN step left
# running is the invoking user -- nothing kills one off Termux,
# --kill-on-exit being a Termux-only proot extension, and on Termux a
# cross-arch step binds $TERMUX_PREFIX (with the whole runtime tree under
# it) for the emulator's loader. Moving the rootfs aside and leaving a
# symlink under the name was therefore enough to make the rest of the
# build read and write somewhere else -- and what it reads goes into a
# layer `push` uploads.
#
# Every test here does the same thing: take the descriptor the engine
# would hold, re-point the name, and check that the work still lands on
# the inode.

import os
import tarfile
from types import SimpleNamespace

import pytest

from proot_distro.helpers import layer_diff
from proot_distro.helpers.build_engine import copy_step, handlers, run_step
from proot_distro.helpers.build_engine.stage import Stage
from proot_distro.helpers.build_engine.users import resolve_user_for_proot


@pytest.fixture
def staged(tmp_path):
    """A stage directory, its rootfs, and a decoy the name can be aimed at."""
    stage_dir = tmp_path / "stage-0"
    rootfs = stage_dir / "rootfs"
    rootfs.mkdir(parents=True)
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    (decoy / "HOST-SECRET").write_bytes(b"host content\n")

    dir_fd = os.open(str(stage_dir), os.O_RDONLY | os.O_DIRECTORY)
    rootfs_fd = os.open(str(rootfs), os.O_RDONLY | os.O_DIRECTORY)
    stage = Stage(index=0, name=None, rootfs_dir=str(rootfs),
                  target_arch_pd="x86_64",
                  dir_fd=dir_fd, rootfs_fd=rootfs_fd)
    try:
        yield stage, rootfs, decoy
    finally:
        stage.close()


def _repoint(rootfs, decoy):
    """Swap the rootfs *name* for a symlink to the decoy, as a step could."""
    moved = str(rootfs) + ".moved"
    os.rename(str(rootfs), moved)
    os.symlink(str(decoy), str(rootfs))
    return moved


# --- the snapshots that straddle a RUN step --------------------------------

def test_snapshot_reads_the_pinned_inode(staged):
    stage, rootfs, decoy = staged
    (rootfs / "real").write_bytes(b"x")
    _repoint(rootfs, decoy)

    snap = layer_diff.snapshot(stage.rootfs_dir, rootfs_fd=stage.rootfs_fd)

    assert "real" in snap
    assert "HOST-SECRET" not in snap


def test_snapshot_without_a_pin_still_follows_the_name(staged):
    # The other half of the assertion above: the name really does lead
    # somewhere else by then, so the pin is what makes the difference.
    stage, rootfs, decoy = staged
    (rootfs / "real").write_bytes(b"x")
    _repoint(rootfs, decoy)

    snap = layer_diff.snapshot(stage.rootfs_dir)

    assert "HOST-SECRET" in snap


# --- the layer the step produces -------------------------------------------

def test_layer_packs_the_pinned_inode(staged, tmp_path):
    stage, rootfs, decoy = staged
    (rootfs / "real").write_bytes(b"image content\n")
    _repoint(rootfs, decoy)

    out = tmp_path / "layer.tar.gz"
    layer_diff.write_layer_tar(
        stage.rootfs_dir, ["real", "HOST-SECRET"], [], str(out),
        rootfs_fd=stage.rootfs_fd,
    )

    with tarfile.open(str(out), "r:gz") as tf:
        names = tf.getnames()
        assert "real" in names
        assert "HOST-SECRET" not in names


# --- COPY/ADD's destination ------------------------------------------------

def _entry(payload):
    return {"kind": "file", "root": str(payload.parent),
            "rel": (payload.name,), "src": str(payload),
            "mode": 0o644, "uid": 0, "gid": 0, "mtime": 0,
            "size": payload.stat().st_size}


def test_materialise_writes_into_the_pinned_inode(staged, tmp_path):
    stage, rootfs, decoy = staged
    payload = tmp_path / "payload"
    payload.write_bytes(b"copied\n")
    moved = _repoint(rootfs, decoy)

    copy_step._materialise_files(
        stage.rootfs_dir, {"opt/app": _entry(payload)},
        rootfs_fd=stage.rootfs_fd,
    )

    assert open(os.path.join(moved, "opt", "app"), "rb").read() == b"copied\n"
    assert not os.path.exists(str(decoy / "opt"))


# --- COPY --from=<stage>, where the stage rootfs is the *source* -----------

def test_copy_from_a_pinned_stage_reads_the_pinned_inode(staged):
    stage, rootfs, decoy = staged
    (rootfs / "app").mkdir()
    (rootfs / "app" / "bin").write_bytes(b"image content\n")
    _repoint(rootfs, decoy)

    file_map = {}
    copy_step._copy_from_rootfs(
        stage.rootfs_dir, "/app", "/out", True, file_map, 0, 0, None,
        stage.rootfs_fd,
    )

    with layer_diff.MapSources() as sources:
        entry = file_map["out/bin"]
        fd, _st = sources.open(entry)
        try:
            assert os.read(fd, 64) == b"image content\n"
        finally:
            os.close(fd)


# --- WORKDIR ---------------------------------------------------------------

def test_workdir_creates_inside_the_pinned_inode(staged, tmp_path):
    stage, rootfs, decoy = staged
    moved = _repoint(rootfs, decoy)
    engine = SimpleNamespace(current=stage, tmp_root=str(tmp_path))

    handlers.do_workdir(engine, {
        "name": "WORKDIR", "value": "/srv/app", "exec_form": False,
        "flags": {}, "heredocs": [], "lineno": 1,
    })

    assert os.path.isdir(os.path.join(moved, "srv", "app"))
    assert not os.path.exists(str(decoy / "srv"))


# --- USER, which decides the uid proot runs the step as --------------------

def test_user_is_resolved_out_of_the_pinned_inode(staged):
    stage, rootfs, decoy = staged
    (rootfs / "etc").mkdir()
    (rootfs / "etc" / "passwd").write_text("app:x:1234:1234::/home/app:/bin/sh\n")
    (decoy / "etc").mkdir()
    (decoy / "etc" / "passwd").write_text("app:x:0:0::/root:/bin/sh\n")
    _repoint(rootfs, decoy)

    assert resolve_user_for_proot(stage.rootfs_dir, "app",
                                  root_fd=stage.rootfs_fd) == (1234, 1234)


# --- the argv proot is handed ----------------------------------------------

def test_run_hands_proot_a_relative_root_and_chdirs_into_the_pin(
    staged, tmp_path, monkeypatch
):
    stage, rootfs, _decoy = staged
    monkeypatch.setattr(run_step, "IS_TERMUX", False)
    monkeypatch.setattr(run_step, "get_proot_bin", lambda: "/usr/bin/proot")
    monkeypatch.setattr(run_step, "get_emulator_args", lambda *a: [])
    monkeypatch.setattr(run_step, "get_device_cpu_arch", lambda: "x86_64")

    seen = {}

    class _Proc:
        pid = os.getpid()

        def wait(self, timeout=None):
            return 0

    def _popen(args, **kw):
        seen["args"] = list(args)
        seen["preexec"] = kw.get("preexec_fn")
        return _Proc()

    monkeypatch.setattr(run_step.subprocess, "Popen", _popen)
    monkeypatch.setattr(run_step, "_wait_for_step", lambda *a, **k: None)
    monkeypatch.setattr(run_step, "_stop_step", lambda *a, **k: 0)

    engine = SimpleNamespace(quiet=True, verbose=False, emulator="",
                             tmp_root=str(tmp_path))
    run_step._exec_proot(engine, stage, ["true"], None)

    assert f"--rootfs={os.curdir}" in seen["args"]
    assert not any(a.startswith(f"--rootfs={stage.rootfs_dir}")
                   for a in seen["args"])

    # The hook is what makes "." mean the pinned inode. It runs in the
    # forked child in production; here it runs in place, with the
    # working directory restored from a descriptor afterwards.
    hook = seen["preexec"]
    assert hook is not None
    saved = os.open(os.curdir, os.O_RDONLY | os.O_DIRECTORY)
    try:
        hook()
        assert (os.stat(os.curdir).st_ino
                == os.fstat(stage.rootfs_fd).st_ino)
    finally:
        os.fchdir(saved)
        os.close(saved)


# --- the scratch root the stage directories are made under -----------------

def test_stage_dirs_are_made_off_the_scratch_descriptor(tmp_path):
    from proot_distro.helpers.build_engine.engine import BuildEngine

    scratch = tmp_path / "scratch"
    scratch.mkdir()
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    scratch_fd = os.open(str(scratch), os.O_RDONLY | os.O_DIRECTORY)
    engine = BuildEngine.__new__(BuildEngine)
    engine.tmp_root = str(scratch)
    engine.tmp_root_fd = scratch_fd
    try:
        os.rename(str(scratch), str(scratch) + ".moved")
        os.symlink(str(decoy), str(scratch))

        stage_fd, rootfs_fd = engine._make_stage_dirs(0)
        os.close(stage_fd)
        os.close(rootfs_fd)

        assert os.path.isdir(
            os.path.join(str(scratch) + ".moved", "stage-0", "rootfs")
        )
        assert os.listdir(str(decoy)) == []
    finally:
        os.close(scratch_fd)
        if os.path.islink(str(scratch)):
            os.unlink(str(scratch))
