# Containment tests for proot_distro.helpers.build_engine.copy_step — COPY/ADD
# sources must not escape the build context or source rootfs, and ADD's tar
# auto-extraction must drop traversal members.

from types import SimpleNamespace

import pytest

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
    copy_step._extract_tar_into_dest(str(arc), "extracted", file_map, 0, 0)

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
