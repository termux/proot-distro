# Containment tests for proot_distro.helpers.tar_extract.extract_tar_to_rootfs.
#
# These feed deliberately hostile archives (the kind a malicious third-party
# tar / OCI layer could ship) and assert that nothing is ever written outside
# the destination rootfs and that the documented in-rootfs effect holds.

import os
import stat

import pytest

from proot_distro.helpers.tar_extract import extract_tar_to_rootfs


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "secret"
    sentinel.write_text("SECRET")
    return tmp_path, str(root), sentinel


def _extract(tmp_path, root, members, **kw):
    arc = tmp_path / "evil.tar"
    from _builders import make_tar
    make_tar(str(arc), members)
    extract_tar_to_rootfs(str(arc), root, **kw)


def _assert_no_escape(tmp_path, sentinel):
    # Sentinel untouched; nothing created outside the rootfs.
    assert sentinel.read_text() == "SECRET"
    # The only top-level entries are the rootfs, the sentinel dir, and the
    # archive we wrote — no escaped members landed here.
    assert set(os.listdir(tmp_path)) <= {"root", "outside", "evil.tar"}
    assert os.listdir(str(tmp_path / "outside")) == ["secret"]
    # Classic escape landing spots (one and two levels up) stay empty.
    parent = os.path.dirname(str(tmp_path))
    assert not os.path.exists(os.path.join(parent, "escape"))
    assert not os.path.exists(os.path.join(str(tmp_path), "escape"))
    assert not os.path.exists(os.path.join(str(tmp_path), "escape2"))


def test_dotdot_member_dropped(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "../../escape", "type": "file", "data": b"PWNED"},
        {"name": "../escape2", "type": "file", "data": b"PWNED"},
    ])
    _assert_no_escape(tmp_path, sentinel)
    assert os.listdir(root) == []


def test_nested_dotdot_dropped(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "a/../../b", "type": "file", "data": b"X"},
    ])
    _assert_no_escape(tmp_path, sentinel)
    assert not os.path.exists(os.path.join(root, "b"))


def test_empty_component_dropped(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "a//b", "type": "file", "data": b"X"},
    ])
    assert not os.path.exists(os.path.join(root, "a", "b"))


def test_leading_slash_lands_inside_rootfs(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "/etc/passwd", "type": "file", "data": b"INSIDE"},
    ])
    # The leading slash is stripped; the file lands inside the rootfs, never
    # at the host's /etc/passwd.
    assert open(os.path.join(root, "etc", "passwd"), "rb").read() == b"INSIDE"
    _assert_no_escape(tmp_path, sentinel)


def test_hardlink_linkname_traversal_not_copied(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "grab", "type": "hardlink", "linkname": "../../../etc/shadow"},
    ])
    # The hostile hard link is dropped entirely — no host file is copied in.
    assert not os.path.exists(os.path.join(root, "grab"))
    _assert_no_escape(tmp_path, sentinel)


def test_valid_hardlink_copied_after_regular_files(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "src", "type": "file", "data": b"CONTENT"},
        {"name": "hl", "type": "hardlink", "linkname": "src"},
    ])
    assert open(os.path.join(root, "hl"), "rb").read() == b"CONTENT"


def test_symlink_absolute_target_stays_inside(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "link", "type": "symlink", "linkname": "/etc/passwd"},
    ])
    # Extraction only creates the symlink *inside* the rootfs; it writes
    # nothing to the host. (Runtime confinement is proot's responsibility.)
    link = os.path.join(root, "link")
    assert os.path.islink(link)
    assert os.readlink(link) == "/etc/passwd"
    _assert_no_escape(tmp_path, sentinel)


def test_symlink_dotdot_target_stays_inside(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "link", "type": "symlink", "linkname": "../../../../etc/x"},
    ])
    assert os.path.islink(os.path.join(root, "link"))
    _assert_no_escape(tmp_path, sentinel)


@pytest.mark.xfail(
    strict=True,
    reason="SECURITY GAP: extract_tar_to_rootfs filters '..' in member names "
           "but follows a symlinked parent component. A hostile archive that "
           "first creates 'evil' -> /abs/existing-dir and then writes "
           "'evil/<file>' escapes the rootfs and writes through the symlink "
           "to the host. Fix: refuse to descend into a path component that is "
           "a symlink (e.g. O_NOFOLLOW per component / secure-join). Strict "
           "xfail flips to a failure once the escape is blocked.",
)
def test_writethrough_absolute_symlink_to_existing_dir_blocked(env):
    # The destination dir the symlink points at must exist for the host write
    # to land — the env fixture's `outside/` (holding the sentinel) is one.
    tmp_path, root, sentinel = env
    outside = str(tmp_path / "outside")
    _extract(tmp_path, root, [
        {"name": "evil", "type": "symlink", "linkname": outside},  # abs target
        {"name": "evil/pwned", "type": "file", "data": b"ESCAPED"},
    ])
    # Secure invariant: nothing is written through the symlink onto the host.
    assert not os.path.exists(os.path.join(outside, "pwned"))
    # And the sentinel beside it is untouched.
    assert sentinel.read_text() == "SECRET"


def test_writethrough_absolute_symlink_to_missing_dir_no_escape(env):
    # When the absolute target does not exist, the write must not create it
    # outside the rootfs (extraction may instead abort — also acceptable).
    tmp_path, root, sentinel = env
    missing = str(tmp_path / "nonexistent_target")
    try:
        _extract(tmp_path, root, [
            {"name": "evil", "type": "symlink", "linkname": missing},
            {"name": "evil/pwned", "type": "file", "data": b"ESCAPED"},
        ])
    except OSError:
        pass  # aborting on the dangling-symlink collision is a safe outcome
    assert not os.path.exists(os.path.join(missing, "pwned"))


def test_device_and_fifo_members_skipped(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "dev/null", "type": "chr", "devmajor": 1, "devminor": 3},
        {"name": "dev/sda", "type": "blk", "devmajor": 8, "devminor": 0},
        {"name": "dev/fifo", "type": "fifo"},
    ])
    assert not os.path.exists(os.path.join(root, "dev", "null"))
    assert not os.path.exists(os.path.join(root, "dev", "sda"))
    assert not os.path.exists(os.path.join(root, "dev", "fifo"))


def test_dot_prefixed_paths_collapse_safely(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "./etc/hostname", "type": "file", "data": b"guest"},
        {"name": ".", "type": "dir"},
    ])
    assert open(os.path.join(root, "etc", "hostname"), "rb").read() == b"guest"
    _assert_no_escape(tmp_path, sentinel)


def test_strip_count_drops_wrapper_dir(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "wrapper/etc/hostname", "type": "file", "data": b"g"},
    ], strip=1)
    assert open(os.path.join(root, "etc", "hostname"), "rb").read() == b"g"


def test_directory_mode_hardened(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "locked", "type": "dir", "mode": 0o000},
    ])
    mode = stat.S_IMODE(os.stat(os.path.join(root, "locked")).st_mode)
    assert (mode & stat.S_IRWXU) == stat.S_IRWXU


def test_whiteout_removes_sibling(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "keep", "type": "file", "data": b"k"},
        {"name": "gone", "type": "file", "data": b"g"},
        {"name": ".wh.gone", "type": "file"},
    ], handle_whiteouts=True)
    assert os.path.exists(os.path.join(root, "keep"))
    assert not os.path.exists(os.path.join(root, "gone"))


def test_opaque_whiteout_clears_parent_only(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "top", "type": "file", "data": b"t"},
        {"name": "d", "type": "dir"},
        {"name": "d/a", "type": "file", "data": b"a"},
        {"name": "d/b", "type": "file", "data": b"b"},
        {"name": "d/.wh..wh..opq", "type": "file"},
    ], handle_whiteouts=True)
    assert os.path.isdir(os.path.join(root, "d"))
    assert not os.path.exists(os.path.join(root, "d", "a"))
    assert not os.path.exists(os.path.join(root, "d", "b"))
    # Siblings of the opaque dir are untouched.
    assert os.path.exists(os.path.join(root, "top"))


def test_whiteouts_ignored_when_disabled(env):
    tmp_path, root, sentinel = env
    # With handle_whiteouts=False (plain rootfs tar), .wh. files are simply
    # skipped, not interpreted — and the target survives.
    _extract(tmp_path, root, [
        {"name": "gone", "type": "file", "data": b"g"},
        {"name": ".wh.gone", "type": "file"},
    ], handle_whiteouts=False)
    assert os.path.exists(os.path.join(root, "gone"))


def test_kitchen_sink_hostile_archive_contained(env):
    tmp_path, root, sentinel = env
    _extract(tmp_path, root, [
        {"name": "../../escape", "type": "file", "data": b"P"},
        {"name": "/abs/evil", "type": "file", "data": b"P"},
        {"name": "ok/file", "type": "file", "data": b"OK"},
        {"name": "bad", "type": "hardlink", "linkname": "../../etc/shadow"},
        {"name": "sl", "type": "symlink", "linkname": "/etc/passwd"},
        {"name": "dev/x", "type": "chr"},
    ])
    _assert_no_escape(tmp_path, sentinel)
    assert open(os.path.join(root, "ok", "file"), "rb").read() == b"OK"
    # The absolute path was re-rooted inside, not written to the host.
    assert os.path.exists(os.path.join(root, "abs", "evil"))
