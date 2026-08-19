# Containment tests for the destination side of a build instruction.
#
# WORKDIR and COPY/ADD both normalised only a *relative* path, so an
# absolute one carried its ".." straight through: WORKDIR composed a
# host path above the rootfs and os.makedirs() created it there, and
# COPY's arcname reached the layer packer, which had no filter of its
# own. A layer is the artefact that leaves the machine, so what it
# contains matters even where nothing here would apply it.

import os
import tarfile
from types import SimpleNamespace

import pytest

from proot_distro.helpers.build_engine import handlers
from proot_distro.helpers.build_engine.copy_step import (
    _dest_arcname, _materialise_files,
)
from proot_distro.helpers.layer_diff import layer_path_parts, write_files_layer


# --- WORKDIR ---------------------------------------------------------------

def _workdir_engine(tmp_path):
    rootfs = tmp_path / "containers" / "box" / "rootfs"
    rootfs.mkdir(parents=True)
    tmp_root = tmp_path / "tmp"
    tmp_root.mkdir()
    stage = SimpleNamespace(rootfs_dir=str(rootfs), workdir="/",
                            image_config={}, index=0, layers=[])
    return SimpleNamespace(current=stage, tmp_root=str(tmp_root)), stage, rootfs


@pytest.mark.parametrize("path,expected", [
    ("/../escape", "/escape"),
    ("/../../../../escape", "/escape"),
    ("/opt/../escape", "/escape"),
    ("/opt/app", "/opt/app"),
])
def test_workdir_absolute_is_clamped_at_the_image_root(tmp_path, path, expected):
    engine, stage, rootfs = _workdir_engine(tmp_path)

    handlers.do_workdir(engine, {"value": path, "lineno": 1})

    assert stage.image_config["config"]["WorkingDir"] == expected
    assert stage.workdir == expected
    # Nothing was created outside the rootfs — not beside it, not above it.
    assert sorted(os.listdir(str(tmp_path))) == ["containers", "tmp"]
    assert os.listdir(str(rootfs.parent)) == ["rootfs"]
    assert os.path.isdir(os.path.join(str(rootfs), expected.lstrip("/")))


def test_workdir_layer_carries_no_traversal_arcname(tmp_path):
    engine, stage, _rootfs = _workdir_engine(tmp_path)

    handlers.do_workdir(engine, {"value": "/../escape", "lineno": 1})

    from proot_distro.helpers.docker import layer_cache_path
    assert stage.layers, "WORKDIR should still emit its thin layer"
    with tarfile.open(layer_cache_path(stage.layers[0]["digest"])) as tf:
        names = [m.name for m in tf.getmembers()]
    assert names == ["escape"]


def test_workdir_relative_still_resolves_against_the_current_one(tmp_path):
    engine, stage, _rootfs = _workdir_engine(tmp_path)
    stage.workdir = "/opt"

    handlers.do_workdir(engine, {"value": "app", "lineno": 1})

    assert stage.workdir == "/opt/app"


# --- COPY/ADD destinations -------------------------------------------------

def test_absolute_dest_keeps_its_trailing_slash_through_normalisation():
    # normpath strips it, and _copy_url / _dest_arcname / _add_directory_tree
    # all read it back, so restoring it is what keeps `COPY x /opt/app/`
    # meaning the same thing it did before.
    assert _dest_arcname("/ctx/x", "/opt/app/", False) == "opt/app/x"
    assert _dest_arcname("/ctx/x", "/opt/app", False) == "opt/app"


def test_materialise_and_packer_agree_on_a_traversal_arcname(tmp_path):
    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    sibling = tmp_path / "sibling"
    sibling.write_text("KEEP")
    file_map = {
        "../foo": {"kind": "content", "data": b"P", "mode": 0o644,
                   "uid": 0, "gid": 0, "mtime": 0},
        "ok/f": {"kind": "content", "data": b"K", "mode": 0o644,
                 "uid": 0, "gid": 0, "mtime": 0},
    }

    _materialise_files(str(rootfs), file_map)
    out = tmp_path / "layer.tar.gz"
    write_files_layer(file_map, str(out))

    # Neither half acted on it, and the packer did not invent a ".."
    # ancestor for it either.
    assert sorted(os.listdir(str(tmp_path))) == ["layer.tar.gz", "rootfs",
                                                 "sibling"]
    assert sibling.read_text() == "KEEP"
    with tarfile.open(str(out)) as tf:
        assert [m.name for m in tf.getmembers()] == ["ok", "ok/f"]
    assert os.path.isfile(os.path.join(str(rootfs), "ok", "f"))


@pytest.mark.parametrize("arcname", ["../foo", "a/../../b", "..", "", ".",
                                     "./.."])
def test_layer_path_parts_refuses_an_escaping_name(arcname):
    assert layer_path_parts(arcname) is None


@pytest.mark.parametrize("arcname,parts", [
    ("a/b", ["a", "b"]),
    ("./a/b", ["a", "b"]),
    ("a//b", ["a", "b"]),
    ("a/./b", ["a", "b"]),
])
def test_layer_path_parts_keeps_an_ordinary_name(arcname, parts):
    assert layer_path_parts(arcname) == parts
