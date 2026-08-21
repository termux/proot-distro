# Containment tests for the local OCI-image install path
# (proot_distro.commands.install_local) — crafted digests in index.json must
# not escape the blob layout, and hostile layer content must stay in rootfs.

import os
import tarfile

import pytest

from proot_distro.commands import install_local


def test_oci_blob_path_valid():
    assert install_local._oci_blob_path("sha256:abc123") == "blobs/sha256/abc123"


@pytest.mark.parametrize("digest", [
    "sha256:../../../etc/passwd",
    "../foo:bar",
    "sha256:dead/beef",
    "sha256:",
])
def test_oci_blob_path_rejects_crafted_digest(digest):
    with pytest.raises(RuntimeError):
        install_local._oci_blob_path(digest)


def test_install_oci_with_traversal_index_digest_fails(tmp_path, builders):
    root = tmp_path / "rootfs"
    root.mkdir()
    arc = tmp_path / "img.oci.tar"
    builders.make_oci_archive(
        str(arc),
        [[{"name": "etc/hostname", "type": "file", "data": b"g"}]],
        bad_index_digest="sha256:../../../etc/passwd",
    )
    # The crafted index manifest digest is rejected by validate_digest before
    # any path is built from it.
    with pytest.raises(RuntimeError):
        install_local.install_from_local_file(str(arc), str(root), "x86_64")


def test_install_oci_outer_hardlink_layer_rejected(tmp_path, builders):
    """A hardlink in the outer OCI archive shadowing a layer blob is rejected.

    Python's tarfile.extractfile() silently follows LNKTYPE members to
    their targets within the archive, so without an explicit isreg() guard
    a crafted outer tar could swap one image's layer for another's without
    any digest check catching the substitution.
    """
    root = tmp_path / "rootfs"
    root.mkdir()
    meta = builders.make_oci_archive(
        str(tmp_path / "base.oci.tar"),
        [[{"name": "etc/hostname", "type": "file", "data": b"ok"}]],
    )
    layer_hex = meta["layer_digests"][0].split(":")[1]
    arc = tmp_path / "evil.oci.tar"
    builders.make_oci_archive(
        str(arc),
        [[{"name": "etc/hostname", "type": "file", "data": b"ok"}]],
        outer_extra_members=[{
            "name": "blobs/sha256/" + layer_hex,
            "type": "hardlink",
            "linkname": "index.json",
        }],
    )
    with pytest.raises(RuntimeError, match="not a regular file"):
        install_local.install_from_local_file(str(arc), str(root), "x86_64")


def test_install_oci_outer_symlink_layer_rejected(tmp_path, builders):
    """A symlink in the outer OCI archive shadowing a layer blob is rejected."""
    root = tmp_path / "rootfs"
    root.mkdir()
    meta = builders.make_oci_archive(
        str(tmp_path / "base.oci.tar"),
        [[{"name": "etc/hostname", "type": "file", "data": b"ok"}]],
    )
    layer_hex = meta["layer_digests"][0].split(":")[1]
    arc = tmp_path / "evil.oci.tar"
    builders.make_oci_archive(
        str(arc),
        [[{"name": "etc/hostname", "type": "file", "data": b"ok"}]],
        outer_extra_members=[{
            "name": "blobs/sha256/" + layer_hex,
            "type": "symlink",
            "linkname": "index.json",
        }],
    )
    with pytest.raises(RuntimeError, match="not a regular file"):
        install_local.install_from_local_file(str(arc), str(root), "x86_64")


def test_install_oci_outer_hardlink_index_rejected(tmp_path, builders):
    """A hardlink shadowing index.json in the outer OCI archive is rejected."""
    root = tmp_path / "rootfs"
    root.mkdir()
    meta = builders.make_oci_archive(
        str(tmp_path / "base.oci.tar"),
        [[{"name": "etc/hostname", "type": "file", "data": b"ok"}]],
    )
    layer_hex = meta["layer_digests"][0].split(":")[1]
    arc = tmp_path / "evil.oci.tar"
    builders.make_oci_archive(
        str(arc),
        [[{"name": "etc/hostname", "type": "file", "data": b"ok"}]],
        outer_extra_members=[{
            "name": "index.json",
            "type": "hardlink",
            "linkname": "blobs/sha256/" + layer_hex,
        }],
    )
    with pytest.raises(RuntimeError, match="not a regular file"):
        install_local.install_from_local_file(str(arc), str(root), "x86_64")


def test_install_oci_with_hostile_layer_contained(tmp_path, builders):
    base, root = tmp_path, tmp_path / "rootfs"
    root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sentinel = outside / "secret"
    sentinel.write_text("SECRET")

    arc = tmp_path / "img.oci.tar"
    builders.make_oci_archive(str(arc), [[
        {"name": "../../escape", "type": "file", "data": b"PWNED"},
        {"name": "etc/ok", "type": "file", "data": b"OK"},
        {"name": "evil", "type": "hardlink", "linkname": "../../../etc/shadow"},
    ]], arch="x86_64")

    meta = install_local.install_from_local_file(str(arc), str(root), "x86_64")

    assert open(os.path.join(str(root), "etc", "ok"), "rb").read() == b"OK"
    assert not os.path.exists(os.path.join(str(root), "evil"))
    assert sentinel.read_text() == "SECRET"
    assert not os.path.exists(os.path.join(os.path.dirname(str(base)), "escape"))
    # Local OCI returns metadata so install can write manifest.json.
    assert meta is not None
    assert meta["arch"] == "x86_64"


# ---------------------------------------------------------------------------
# Bounded indexing and bounded reads.
# ---------------------------------------------------------------------------
#
# The archive is a stranger's -- `install ./img.tar`, or an http(s)://
# URL -- and both the number of members it declares and the size it gives
# each one are its own choice. tf.getmembers() held a TarInfo for every
# member and the JSON readers pulled a whole member into memory, so how
# much this process allocates was the archive's to decide.

def _oci_with_extra_members(tmp_path, builders, names):
    """A valid one-layer OCI archive carrying *names* as extra members."""
    arc = tmp_path / "img.oci.tar"
    builders.make_oci_archive(
        str(arc),
        [[{"name": "etc/hostname", "type": "file", "data": b"g"}]],
        outer_extra_members=[
            {"name": n, "type": "file", "data": b""} for n in names
        ],
    )
    return arc


def test_unaddressable_members_are_not_indexed(tmp_path, builders):
    """Only the names _oci_open_member() can ask for get a TarInfo."""
    arc = _oci_with_extra_members(
        tmp_path, builders,
        ["junk/a", "junk/b", "oci-layout.bak", "blobs/sha256/zz-not-hex"],
    )
    with tarfile.open(str(arc), "r:*") as tf:
        member_map = install_local._index_oci_members(tf)
    assert "index.json" in member_map
    assert all(
        name == "index.json" or name.startswith("blobs/sha256/")
        for name in member_map
    )
    assert not any("junk" in name for name in member_map)


def test_member_count_is_capped(tmp_path, builders, monkeypatch):
    monkeypatch.setattr(install_local, "_MAX_OCI_MEMBERS", 4)
    arc = _oci_with_extra_members(
        tmp_path, builders, [f"junk/{i}" for i in range(20)],
    )
    with tarfile.open(str(arc), "r:*") as tf:
        with pytest.raises(RuntimeError, match="more than 4 entries"):
            install_local._index_oci_members(tf)


def test_install_refuses_an_archive_with_too_many_members(
        tmp_path, builders, monkeypatch):
    monkeypatch.setattr(install_local, "_MAX_OCI_MEMBERS", 4)
    root = tmp_path / "rootfs"
    root.mkdir()
    arc = _oci_with_extra_members(
        tmp_path, builders, [f"junk/{i}" for i in range(20)],
    )
    with pytest.raises(RuntimeError, match="refusing to index it"):
        install_local.install_from_local_file(str(arc), str(root), "x86_64")
    # Nothing was applied.
    assert os.listdir(str(root)) == []


def test_oversized_json_member_is_refused(tmp_path, builders, monkeypatch):
    """index.json is read to a ceiling, not to whatever the header says."""
    monkeypatch.setattr(install_local, "_MAX_JSON_BYTES", 64)
    root = tmp_path / "rootfs"
    root.mkdir()
    arc = tmp_path / "img.oci.tar"
    builders.make_oci_archive(
        str(arc), [[{"name": "etc/hostname", "type": "file", "data": b"g"}]],
    )
    with pytest.raises(RuntimeError, match="larger than 64 bytes"):
        install_local.install_from_local_file(str(arc), str(root), "x86_64")
    assert os.listdir(str(root)) == []
