# Containment tests for statedir, the walk every module uses to reach a
# directory of the program's own state tree.
#
# RUNTIME_DIR and BASE_CACHE_DIR are the trust roots; everything below
# them is guest-writable on Termux, where both sit under the
# $TERMUX_PREFIX bound read-write into every non-isolated container. A
# planted `containers/<name>`, `cache` or `build-tmp` symlink is enough
# to have this program write -- or delete -- inside whatever it leads
# to, so no component below a root may be reached by name.

import errno
import os

import pytest

from proot_distro import statedir
from proot_distro.constants import BASE_CACHE_DIR, CONTAINERS_DIR, RUNTIME_DIR


@pytest.fixture
def outside(tmp_path):
    d = tmp_path / "outside"
    d.mkdir()
    (d / "keepsake").write_text("host content\n")
    return d


# --- splitting -------------------------------------------------------------

def test_split_recognises_both_roots():
    root, parts = statedir.split_state_path(
        os.path.join(RUNTIME_DIR, "containers", "box", "rootfs"))
    assert (root, parts) == (RUNTIME_DIR, ("containers", "box", "rootfs"))
    root, parts = statedir.split_state_path(
        os.path.join(BASE_CACHE_DIR, "oci_layers"))
    assert os.path.join(root, *parts) == os.path.join(
        BASE_CACHE_DIR, "oci_layers")


def test_a_root_itself_has_no_parts():
    assert statedir.split_state_path(RUNTIME_DIR) == (RUNTIME_DIR, ())
    assert statedir.split_state_path(RUNTIME_DIR + os.sep) == (RUNTIME_DIR, ())


def test_a_path_outside_the_tree_is_not_ours():
    assert statedir.split_state_path("/etc/passwd") == (None, None)
    assert not statedir.is_state_path("/etc/passwd")
    assert statedir.is_state_path(CONTAINERS_DIR)


def test_dot_dot_cannot_survive_into_the_walk():
    # Normalised first, so a path that climbs out of the root lands
    # outside it rather than becoming a walk that opens '..'.
    climbing = os.path.join(CONTAINERS_DIR, "..", "..", "..", "etc")
    assert statedir.split_state_path(climbing) == (None, None)
    inner = os.path.join(CONTAINERS_DIR, "box", "..", "other")
    root, parts = statedir.split_state_path(inner)
    assert parts == ("containers", "other")


def test_open_refuses_a_path_outside_the_tree():
    with pytest.raises(ValueError):
        statedir.open_state_dir("/etc")


# --- walking ---------------------------------------------------------------

def _close(fd):
    os.close(fd)


def test_symlinked_component_is_refused(outside):
    os.symlink(str(outside), os.path.join(CONTAINERS_DIR, "box"))
    with pytest.raises(OSError) as exc:
        statedir.open_state_dir(os.path.join(CONTAINERS_DIR, "box"))
    assert exc.value.errno == errno.ENOTDIR


def test_symlinked_component_is_refused_while_creating(outside):
    os.symlink(str(outside), os.path.join(CONTAINERS_DIR, "box"))
    with pytest.raises(OSError) as exc:
        statedir.open_state_dir(
            os.path.join(CONTAINERS_DIR, "box", "rootfs"), create=True)
    assert exc.value.errno == errno.ENOTDIR
    assert sorted(os.listdir(str(outside))) == ["keepsake"]


def test_a_file_in_the_way_is_refused():
    with open(os.path.join(CONTAINERS_DIR, "box"), "w") as fh:
        fh.write("not a directory")
    with pytest.raises(OSError) as exc:
        statedir.open_state_dir(os.path.join(CONTAINERS_DIR, "box", "rootfs"),
                                create=True)
    assert exc.value.errno == errno.ENOTDIR


def test_missing_component_without_create():
    with pytest.raises(FileNotFoundError):
        statedir.open_state_dir(os.path.join(CONTAINERS_DIR, "nope"))


def test_missing_components_are_created():
    path = os.path.join(CONTAINERS_DIR, "box", "rootfs")
    _close(statedir.open_state_dir(path, create=True))
    assert os.path.isdir(path)


def test_a_root_opens_as_itself():
    fd = statedir.open_state_dir(RUNTIME_DIR)
    try:
        assert os.path.samestat(os.fstat(fd), os.stat(RUNTIME_DIR))
    finally:
        _close(fd)


def test_the_descriptor_names_the_inode_not_the_name(tmp_path):
    path = os.path.join(CONTAINERS_DIR, "box")
    fd = statedir.open_state_dir(path, create=True)
    try:
        os.rename(path, os.path.join(CONTAINERS_DIR, "moved"))
        with open("proof", "w", opener=lambda p, f: os.open(
                p, f | os.O_CREAT, 0o600, dir_fd=fd)) as fh:
            fh.write("still the same directory")
        assert os.path.exists(os.path.join(CONTAINERS_DIR, "moved", "proof"))
    finally:
        _close(fd)


# --- parents ---------------------------------------------------------------

def test_open_parent_gives_the_pair():
    fd, leaf = statedir.open_state_parent(
        os.path.join(CONTAINERS_DIR, "box", "manifest.json"), create=True)
    try:
        assert leaf == "manifest.json"
        assert os.path.samestat(
            os.fstat(fd), os.stat(os.path.join(CONTAINERS_DIR, "box")))
    finally:
        _close(fd)


def test_open_parent_refuses_a_root():
    with pytest.raises(ValueError):
        statedir.open_state_parent(RUNTIME_DIR)


# --- removal ---------------------------------------------------------------

def test_remove_state_tree_removes_the_tree():
    tree = os.path.join(CONTAINERS_DIR, "box", "rootfs", "etc")
    os.makedirs(tree)
    with open(os.path.join(tree, "passwd"), "w") as fh:
        fh.write("root:x:0:0::/root:/bin/sh\n")
    assert statedir.remove_state_tree(os.path.join(CONTAINERS_DIR, "box"))
    assert not os.path.exists(os.path.join(CONTAINERS_DIR, "box"))


def test_remove_state_tree_will_not_follow_a_planted_parent(outside):
    os.symlink(str(outside), os.path.join(CONTAINERS_DIR, "box"))
    assert not statedir.remove_state_tree(
        os.path.join(CONTAINERS_DIR, "box", "rootfs"))
    assert (outside / "keepsake").exists()


def test_remove_state_tree_unlinks_a_planted_entry(outside):
    os.symlink(str(outside), os.path.join(CONTAINERS_DIR, "box"))
    assert statedir.remove_state_tree(os.path.join(CONTAINERS_DIR, "box"))
    # The link went; what it pointed at did not.
    assert not os.path.lexists(os.path.join(CONTAINERS_DIR, "box"))
    assert (outside / "keepsake").exists()


def test_remove_state_tree_of_a_missing_path_is_a_success():
    assert statedir.remove_state_tree(os.path.join(CONTAINERS_DIR, "gone"))


def test_remove_state_tree_reports_what_would_not_go():
    seen = []
    os.symlink(str("/nowhere"), os.path.join(CONTAINERS_DIR, "box"))
    statedir.remove_state_tree(
        os.path.join(CONTAINERS_DIR, "box", "rootfs"),
        on_error=lambda rel, exc: seen.append((rel, exc.errno)),
    )
    assert seen and seen[0][1] == errno.ENOTDIR
