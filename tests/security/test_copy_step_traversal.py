# Containment tests for proot_distro.helpers.build_engine.copy_step — COPY/ADD
# sources must not escape the build context or source rootfs, and ADD's tar
# auto-extraction must drop traversal members.
#
# Containment used to be decided lexically, on the spelling of the composed
# path, which says nothing about the symlinks standing in it: `escape -> /`
# in the build context (or in the image a COPY --from names) passed the
# prefix check and the instruction then read the host's file and packed it
# into the layer `push` uploads. What confines a source now is the same
# clamped walk the tar extractor resolves a member with, and what reads it
# is a re-walk of that answer off a descriptor.

import os
import tarfile
from types import SimpleNamespace

import pytest

from proot_distro.helpers import layer_diff
from proot_distro.helpers.build_engine import copy_step
from proot_distro.helpers.build_engine.errors import BuildError


def _engine(build_dir):
    return SimpleNamespace(build_dir=str(build_dir), ignore_patterns=[])


def test_copy_from_context_escape_rejected(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    eng = _engine(ctx)
    with pytest.raises(BuildError) as exc:
        copy_step._copy_from_context(
            eng, "../../etc/passwd", "/dest", False, {}, 0, 0, None, False,
        )
    assert "escapes the build context" in str(exc.value)


def test_copy_from_context_valid_source(tmp_path):
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    (ctx / "file.txt").write_text("hi")
    eng = _engine(ctx)
    file_map = {}
    copy_step._copy_from_context(
        eng, "file.txt", "/dest/file.txt", False, file_map, 0, 0, None, False,
    )
    assert "dest/file.txt" in file_map


def test_copy_from_rootfs_escape_rejected(tmp_path):
    src_rootfs = tmp_path / "stage"
    src_rootfs.mkdir()
    with pytest.raises(BuildError) as exc:
        copy_step._copy_from_rootfs(
            str(src_rootfs), "../../etc", "/dest", False, {}, 0, 0, None,
        )
    assert "escapes the source rootfs" in str(exc.value)


def test_add_tar_extract_drops_traversal(tmp_path, builders):
    arc = tmp_path / "payload.tar"
    builders.make_tar(str(arc), [
        {"name": "../evil", "type": "file", "data": b"P"},
        {"name": "./../evil2", "type": "file", "data": b"P"},
        {"name": "subdir/../../evil3", "type": "file", "data": b"P"},
        {"name": "ok", "type": "file", "data": b"OK"},
        {"name": "nested/file", "type": "file", "data": b"N"},
        {"name": "/abs", "type": "file", "data": b"A"},
    ])
    file_map = {}
    spool = tmp_path / "spool"
    spool.mkdir()
    with open(str(arc), "rb") as fh:
        copy_step._extract_tar_into_dest(
            fh, "extracted", file_map, 0, 0, str(spool))

    keys = set(file_map.keys())
    # No key escapes via ".." and every key is confined under the dest prefix.
    assert all(".." not in k for k in keys)
    assert all(k.startswith("extracted/") for k in keys)
    assert "extracted/ok" in keys
    assert "extracted/nested/file" in keys
    # The absolute member is re-rooted under dest, not escaped.
    assert "extracted/abs" in keys
    # The traversal members were dropped.
    assert not any("evil" in k for k in keys)


# --- symlinked parents ------------------------------------------------------

@pytest.fixture
def outside(tmp_path):
    """A host directory the build has no business reading."""
    d = tmp_path / "outside"
    d.mkdir()
    (d / "secret").write_text("host secret")
    return d


def test_context_symlink_out_is_not_a_source(tmp_path, outside):
    # ctx/escape -> <outside>. The path spells itself inside the context
    # at every component, and the file is really there — through the link.
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    os.symlink(str(outside), str(ctx / "escape"))
    file_map = {}
    with pytest.raises(BuildError) as exc:
        copy_step._copy_from_context(
            _engine(ctx), "escape/secret", "/leaked", False, file_map,
            0, 0, None, False,
        )
    assert "not found in build context" in str(exc.value)
    assert file_map == {}


def test_context_glob_does_not_reach_through_a_symlink(tmp_path, outside):
    # glob() answers on the spelling of a path the same way the old
    # containment check did, so the matches go through the walk too.
    ctx = tmp_path / "ctx"
    ctx.mkdir()
    os.symlink(str(outside), str(ctx / "escape"))
    file_map = {}
    with pytest.raises(BuildError):
        copy_step._copy_from_context(
            _engine(ctx), "escape/*", "/leaked/", True, file_map,
            0, 0, None, False,
        )
    assert file_map == {}


def test_context_symlink_inside_is_still_followed(tmp_path):
    # The clamp is not a ban on symlinks: one that stays inside the
    # context is an ordinary way to spell a path and still resolves.
    ctx = tmp_path / "ctx"
    (ctx / "real").mkdir(parents=True)
    (ctx / "real" / "f").write_text("in-context")
    os.symlink("real", str(ctx / "link"))
    file_map = {}
    copy_step._copy_from_context(
        _engine(ctx), "link/f", "/f", False, file_map, 0, 0, None, False,
    )
    assert file_map["f"]["rel"] == ("real", "f")


def test_rootfs_symlink_out_is_not_a_source(tmp_path, outside):
    # The same link, shipped by the image a COPY --from names: nothing in
    # the Dockerfile or the context has to look unusual for this one.
    src_rootfs = tmp_path / "stage"
    src_rootfs.mkdir()
    os.symlink(str(outside), str(src_rootfs / "escape"))
    file_map = {}
    with pytest.raises(BuildError) as exc:
        copy_step._copy_from_rootfs(
            str(src_rootfs), "/escape/secret", "/leaked", False, file_map,
            0, 0, None,
        )
    assert "not found in stage" in str(exc.value)
    assert file_map == {}


def test_rootfs_absolute_symlink_re_roots_at_the_image(tmp_path):
    # An absolute link inside an image means the guest's "/", which is
    # the rootfs — /usr/bin/python -> /usr/local/bin/python and friends.
    src_rootfs = tmp_path / "stage"
    (src_rootfs / "opt").mkdir(parents=True)
    (src_rootfs / "opt" / "tool").write_text("image content")
    os.symlink("/opt", str(src_rootfs / "link"))
    file_map = {}
    copy_step._copy_from_rootfs(
        str(src_rootfs), "/link/tool", "/tool", False, file_map, 0, 0, None,
    )
    assert file_map["tool"]["rel"] == ("opt", "tool")
    assert file_map["tool"]["root"] == str(src_rootfs)


def test_directory_walk_records_links_rather_than_following_them(tmp_path,
                                                                outside):
    ctx = tmp_path / "ctx"
    (ctx / "tree").mkdir(parents=True)
    (ctx / "tree" / "f").write_text("ok")
    os.symlink(str(outside), str(ctx / "tree" / "escape"))
    file_map = {}
    copy_step._copy_from_context(
        _engine(ctx), "tree", "/dest/", True, file_map, 0, 0, None, False,
    )
    assert file_map["dest/f"]["kind"] == "file"
    assert file_map["dest/escape"]["kind"] == "symlink"
    # Nothing under the link was enumerated, so nothing of it is packed.
    assert not any(k.startswith("dest/escape/") for k in file_map)


# --- the read that comes later ---------------------------------------------

def test_a_parent_re_pointed_after_the_walk_is_refused(tmp_path, outside):
    # An instruction is enumerated whole and consumed afterwards, twice.
    # Both consumers re-walk the recorded components from the tree's root
    # with O_NOFOLLOW, so a component swapped in between is refused
    # instead of quietly sourcing the host's file.
    ctx = tmp_path / "ctx"
    (ctx / "a").mkdir(parents=True)
    (ctx / "a" / "f").write_text("context content")
    (outside / "f").write_text("host secret")

    file_map = {}
    copy_step._copy_from_context(
        _engine(ctx), "a/f", "/f", False, file_map, 0, 0, None, False,
    )
    assert file_map["f"]["rel"] == ("a", "f")

    (ctx / "a" / "f").unlink()
    (ctx / "a").rmdir()
    os.symlink(str(outside), str(ctx / "a"))

    rootfs = tmp_path / "rootfs"
    rootfs.mkdir()
    with pytest.raises(BuildError):
        copy_step._materialise_files(str(rootfs), file_map)
    assert not (rootfs / "f").exists()

    out = tmp_path / "layer.tar.gz"
    layer_diff.write_files_layer(file_map, str(out))
    with tarfile.open(str(out)) as tf:
        assert [m.name for m in tf.getmembers() if m.isreg()] == []
