# Tests for proot_distro.paths — container path layout, the `name:path`
# spec resolver (with traversal confinement), and lock-set construction.

import os

import pytest

from proot_distro import paths


def test_container_dir_layout(paths_mod=paths):
    from proot_distro.constants import CONTAINERS_DIR
    assert paths.container_dir("box") == os.path.join(CONTAINERS_DIR, "box")
    assert paths.container_rootfs("box") == os.path.join(
        CONTAINERS_DIR, "box", "rootfs"
    )
    assert paths.container_manifest("box") == os.path.join(
        CONTAINERS_DIR, "box", "manifest.json"
    )


def test_container_from_spec():
    assert paths.container_from_spec("box:/etc/hosts") == "box"
    assert paths.container_from_spec("/plain/host/path") is None
    assert paths.container_from_spec("box:") == "box"


def test_resolve_plain_path_is_abspath(tmp_path):
    rel = "some/./weird/../path"
    resolved = paths.resolve_container_path(rel)
    assert resolved == os.path.normpath(os.path.abspath(rel))
    assert os.path.isabs(resolved)


def test_resolve_host_path_dereferences_its_own_leaf(tmp_path):
    """A host endpoint names what it points at, as cp and rsync both do.

    The container side gets this from the chroot walk; without the same
    treatment here the two ends of a transfer are resolved to different
    depths, and an overlap the destination link hides goes unnoticed.
    """
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "link"
    os.symlink(str(target), link)
    assert paths.resolve_container_path(str(link)) == str(target)


def test_resolve_host_path_keeps_its_leaf_for_a_move(tmp_path):
    """`copy --move` renames the final name, so it must survive resolution."""
    target = tmp_path / "target"
    target.write_text("x")
    link = tmp_path / "link"
    os.symlink(str(target), link)
    kept = paths.resolve_container_path(str(link), deref_leaf=False)
    assert kept == str(link)


def test_resolve_host_path_resolves_parents_even_for_a_move(tmp_path):
    """Only the last component is exempt; the chain above it is not."""
    real = tmp_path / "real"
    real.mkdir()
    alias = tmp_path / "alias"
    os.symlink(str(real), alias)
    resolved = paths.resolve_container_path(str(alias / "leaf"),
                                            deref_leaf=False)
    assert resolved == str(real / "leaf")


def test_resolve_container_path_inside_rootfs(builders):
    builders.make_container("box")
    resolved = paths.resolve_container_path("box:/etc/passwd")
    expected = os.path.join(paths.container_rootfs("box"), "etc", "passwd")
    assert resolved == os.path.normpath(expected)


def test_resolve_container_path_leading_slash_stripped(builders):
    builders.make_container("box")
    # Both forms land at the same place inside the rootfs.
    a = paths.resolve_container_path("box:/etc")
    b = paths.resolve_container_path("box:etc")
    assert a == b


@pytest.mark.parametrize("rel", ["../../etc/passwd", "../..", "a/../../../x"])
def test_resolve_container_path_rejects_escape(builders, capsys, rel):
    builders.make_container("box")
    with pytest.raises(SystemExit) as exc:
        paths.resolve_container_path(f"box:{rel}")
    assert exc.value.code == 1
    assert "escapes the container directory" in capsys.readouterr().err


def test_resolve_container_path_follows_symlink_inside(builders):
    """A relative link inside the rootfs resolves to its real target."""
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.makedirs(os.path.join(rootfs, "real"))
    os.symlink("real", os.path.join(rootfs, "alias"))
    assert paths.resolve_container_path("box:/alias/f") == os.path.join(
        rootfs, "real", "f"
    )


def test_resolve_container_path_reanchors_absolute_symlink(builders):
    """An absolute link target is read against the rootfs, not the host."""
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.symlink("/usr/share/zoneinfo/UTC", os.path.join(rootfs, "localtime"))
    assert paths.resolve_container_path("box:/localtime") == os.path.join(
        rootfs, "usr", "share", "zoneinfo", "UTC"
    )


def test_resolve_container_path_clamps_dotdot_in_symlink(builders):
    """`..` inside a link target stops at the rootfs instead of escaping."""
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.symlink("../" * 10 + "etc", os.path.join(rootfs, "up"))
    assert paths.resolve_container_path("box:/up/passwd") == os.path.join(
        rootfs, "etc", "passwd"
    )


def test_resolve_container_path_missing_components_kept(builders):
    """A destination that does not exist yet resolves literally."""
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    assert paths.resolve_container_path("box:/new/dir/file") == os.path.join(
        rootfs, "new", "dir", "file"
    )


def test_resolve_container_path_chained_symlinks(builders):
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.makedirs(os.path.join(rootfs, "c"))
    os.symlink("b", os.path.join(rootfs, "a"))
    os.symlink("/c", os.path.join(rootfs, "b"))
    assert paths.resolve_container_path("box:/a/f") == os.path.join(
        rootfs, "c", "f"
    )


def test_resolve_container_path_empty_name_rejected(capsys):
    with pytest.raises(SystemExit) as exc:
        paths.resolve_container_path(":/etc/passwd")
    assert exc.value.code == 1
    assert "invalid container name" in capsys.readouterr().err


def test_resolve_container_path_missing_container(capsys):
    with pytest.raises(SystemExit) as exc:
        paths.resolve_container_path("ghost:/etc")
    assert exc.value.code == 1
    assert "does not exist" in capsys.readouterr().err


# ----- resolve_container_path(deref_leaf=False) ----------------------------

def test_deref_leaf_false_keeps_a_leaf_symlink(builders):
    """`copy --move` renames the entry, so its last component must stand.

    Resolving the leaf would make the move act on the link's target: the
    target left the container, the link stayed behind and dangled.
    """
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.symlink("target", os.path.join(rootfs, "link"))

    assert paths.resolve_container_path("box:/link") == \
        os.path.join(rootfs, "target")
    assert paths.resolve_container_path("box:/link", deref_leaf=False) == \
        os.path.join(rootfs, "link")


def test_deref_leaf_false_still_resolves_parent_symlinks(builders):
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.mkdir(os.path.join(rootfs, "real"))
    os.symlink("/real", os.path.join(rootfs, "dir"))
    os.symlink("x", os.path.join(rootfs, "real", "leaf"))

    assert paths.resolve_container_path("box:/dir/leaf", deref_leaf=False) == \
        os.path.join(rootfs, "real", "leaf")


def test_deref_leaf_false_still_confines_an_absolute_parent_link(builders):
    """Keeping the leaf must not weaken the containment of the parents."""
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.symlink("/", os.path.join(rootfs, "escape"))

    resolved = paths.resolve_container_path("box:/escape/etc/x",
                                            deref_leaf=False)
    assert resolved.startswith(rootfs + os.sep)
    assert resolved == os.path.join(rootfs, "etc", "x")


@pytest.mark.parametrize("spec,tail", [("box:/a/.", "a"), ("box:/a/..", ""),
                                       ("box:/a/", "a"), ("box:", "")])
def test_deref_leaf_false_collapses_dot_components(builders, spec, tail):
    """`.` and `..` name no entry of their own; the full walk handles them."""
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    expected = os.path.join(rootfs, tail) if tail else rootfs
    assert paths.resolve_container_path(spec, deref_leaf=False) == expected


# ----- refuse_src_dest_overlap --------------------------------------------

def test_overlap_allows_a_sibling_with_a_shared_prefix(tmp_path):
    """`tree` and `tree2` share a prefix but do not overlap."""
    paths.refuse_src_dest_overlap("tree", str(tmp_path / "tree"),
                                  "tree2", str(tmp_path / "tree2"))


def test_overlap_rejects_the_same_path(tmp_path, capsys):
    """A file copied onto itself was truncated while still being read."""
    target = tmp_path / "f"
    target.write_text("keep me")
    with pytest.raises(SystemExit) as exc:
        paths.refuse_src_dest_overlap("f", str(target), "f", str(target))
    assert exc.value.code == 1
    assert "same file" in capsys.readouterr().err
    assert target.read_text() == "keep me"


def test_overlap_rejects_two_names_for_one_inode(tmp_path, capsys):
    src = tmp_path / "f"
    src.write_text("x")
    dest = tmp_path / "g"
    os.link(src, dest)
    with pytest.raises(SystemExit):
        paths.refuse_src_dest_overlap("f", str(src), "g", str(dest))
    assert "same file" in capsys.readouterr().err


def test_overlap_rejects_a_destination_inside_the_source(tmp_path, capsys):
    src = tmp_path / "tree"
    src.mkdir()
    with pytest.raises(SystemExit) as exc:
        paths.refuse_src_dest_overlap("tree", str(src),
                                      "tree/in", str(src / "in"))
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err


def test_overlap_allows_a_destination_that_does_not_exist_yet(tmp_path):
    src = tmp_path / "tree"
    src.mkdir()
    paths.refuse_src_dest_overlap("tree", str(src),
                                  "out", str(tmp_path / "out"))


def test_overlap_sees_through_a_host_symlink_parent(tmp_path, capsys):
    """A host path is never walked, so a link among its parents can fold.

    Only the container side arrives symlink-free; comparing host paths
    literally left `copy -r <dir> <link>/inner` recursing to the
    interpreter's limit with `link -> <dir>`.
    """
    src = tmp_path / "tree"
    (src / "sub").mkdir(parents=True)
    link = tmp_path / "link"
    os.symlink(str(src), link)

    with pytest.raises(SystemExit) as exc:
        paths.refuse_src_dest_overlap("tree", str(src),
                                      "link/inner", str(link / "inner"))
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err


def test_overlap_sees_a_host_link_folding_into_a_container_source(
    builders, tmp_path, capsys
):
    """The fold works the other way too: host destination, container source."""
    builders.make_container("box")
    data = os.path.join(paths.container_rootfs("box"), "data")
    os.makedirs(data)
    backdoor = tmp_path / "backdoor"
    os.symlink(data, backdoor)

    with pytest.raises(SystemExit):
        paths.refuse_src_dest_overlap("box:/data", data,
                                      "backdoor/in", str(backdoor / "in"))
    assert "into itself" in capsys.readouterr().err


def test_overlap_resolves_the_prefix_above_the_rootfs(
    builders, tmp_path, monkeypatch, capsys
):
    """A symlink *above* the rootfs must not hide an overlap either.

    The container side is deliberately never handed to realpath -- below
    the rootfs the chroot walk has already resolved everything, and host
    semantics would undo it. But the rootfs itself is only composed
    lexically, so a symlinked $HOME or ~/.local/share left the two sides
    spelled differently and nothing looked like it overlapped: naming one
    container directory as a container path and a directory inside it as a
    host path recursed until the stack gave out.
    """
    builders.make_container("box")
    real_containers = os.path.realpath(paths.CONTAINERS_DIR)
    aliased = tmp_path / "alias"
    os.symlink(real_containers, aliased)
    monkeypatch.setattr(paths, "CONTAINERS_DIR", str(aliased))

    src = paths.resolve_container_path("box:/data")
    assert str(aliased) in src              # the alias really is in play
    os.makedirs(src, exist_ok=True)
    inner = os.path.join(real_containers, "box", "rootfs", "data", "in")

    with pytest.raises(SystemExit) as exc:
        paths.refuse_src_dest_overlap("box:/data", src, "inner", inner)
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err


def test_overlap_leaves_the_final_component_unresolved(tmp_path):
    """Dereferencing the leaf would refuse a legitimate move of a link.

    A link standing where a directory endpoint belongs is refused further
    down instead -- by pin_path for sync, by mkdirat's EEXIST for copy.
    """
    tree = tmp_path / "tree"
    tree.mkdir()
    link = tmp_path / "link"
    os.symlink(str(tree), link)
    paths.refuse_src_dest_overlap("link", str(link),
                                  "link/inner", str(link / "inner"),
                                  deref_leaf=False)


def test_overlap_follows_the_leaf_only_when_the_operation_does(tmp_path,
                                                               capsys):
    """cp refuses `cp f link`; mv renames it. The check has to agree."""
    target = tmp_path / "f"
    target.write_text("data")
    link = tmp_path / "link"
    os.symlink("f", link)

    with pytest.raises(SystemExit):        # copy: same file, as cp says
        paths.refuse_src_dest_overlap("f", str(target), "link", str(link))
    assert "same file" in capsys.readouterr().err

    # move: rename(2) replaces the link, which is what mv does.
    paths.refuse_src_dest_overlap("f", str(target), "link", str(link),
                                  deref_leaf=False)


def test_overlap_refuses_a_hardlinked_pair_in_either_mode(tmp_path, capsys):
    src = tmp_path / "f"
    src.write_text("x")
    hard = tmp_path / "hard"
    os.link(src, hard)
    for deref in (True, False):
        with pytest.raises(SystemExit):
            paths.refuse_src_dest_overlap("f", str(src), "hard", str(hard),
                                          deref_leaf=deref)
        assert "same file" in capsys.readouterr().err


def test_overlap_refuses_a_source_inside_a_pruned_destination(tmp_path,
                                                             capsys):
    """`sync --delete a/b a` deleted a/b: it is an orphan of itself."""
    dest = tmp_path / "a"
    src = dest / "b"
    src.mkdir(parents=True)

    with pytest.raises(SystemExit) as exc:
        paths.refuse_src_dest_overlap("a/b", str(src), "a", str(dest),
                                      pruning=True)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--delete" in err and "inside the destination" in err


def test_overlap_allows_a_source_inside_the_destination_without_pruning(
    tmp_path
):
    """Without --delete nothing is removed, so this is merely unusual."""
    dest = tmp_path / "a"
    src = dest / "b"
    src.mkdir(parents=True)
    paths.refuse_src_dest_overlap("a/b", str(src), "a", str(dest))


def test_overlap_sees_a_host_link_standing_as_the_destination(tmp_path,
                                                             capsys):
    """The endpoint itself, not just its parents, folds one end into the other.

    `sync <dir> <link>` with `link -> <dir>/sub` recursed to the
    interpreter's limit: sync's pin follows a host endpoint link (host paths
    are not walked with O_NOFOLLOW), so the destination really was inside
    the source, and comparing the name hid it.
    """
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    link = tmp_path / "link"
    os.symlink(str(tree / "sub"), link)

    with pytest.raises(SystemExit) as exc:
        paths.refuse_src_dest_overlap("tree", str(tree), "link", str(link))
    assert exc.value.code == 1
    assert "into itself" in capsys.readouterr().err


def test_overlap_sees_a_destination_link_to_the_sources_parent(tmp_path,
                                                              capsys):
    """`sync --delete <src> <link>` with `link -> <src>/..` deleted <src>.

    The source is an orphan of itself once the destination resolves to its
    parent, so the prune pass removed it — along with everything else the
    parent held that the source did not.
    """
    parent = tmp_path / "parent"
    src = parent / "a"
    src.mkdir(parents=True)
    link = tmp_path / "link"
    os.symlink(str(parent), link)

    with pytest.raises(SystemExit) as exc:
        paths.refuse_src_dest_overlap("parent/a", str(src), "link", str(link),
                                      pruning=True)
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "--delete" in err and "inside the destination" in err


# ----- resolve_container_child(deref_leaf=False) --------------------------

def test_child_keeps_an_appended_leaf_symlink(builders):
    """`copy --move f box:/dir` renames onto box:/dir/f and must replace it.

    Resolving the appended name let a guest that planted box:/dir/f as a
    link send the move somewhere else in its rootfs and keep the link.
    """
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.mkdir(os.path.join(rootfs, "dir"))
    os.symlink("/victim", os.path.join(rootfs, "dir", "f"))
    resolved = os.path.join(rootfs, "dir")

    assert paths.resolve_container_child("box:/dir", resolved, "f") == \
        os.path.join(rootfs, "victim")
    assert paths.resolve_container_child("box:/dir", resolved, "f",
                                         deref_leaf=False) == \
        os.path.join(rootfs, "dir", "f")


def test_child_still_resolves_a_symlinked_parent_when_keeping_the_leaf(
    builders
):
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.mkdir(os.path.join(rootfs, "real"))
    os.symlink("/real", os.path.join(rootfs, "dir"))
    resolved = os.path.join(rootfs, "dir")

    assert paths.resolve_container_child("box:/dir", resolved, "f",
                                         deref_leaf=False) == \
        os.path.join(rootfs, "real", "f")


# ----- pin_path diagnostics ----------------------------------------------

def test_pin_path_reports_a_plain_non_directory_as_such(builders, capsys):
    """ENOTDIR from an ordinary file must not be reported as a race.

    O_NOFOLLOW|O_DIRECTORY on a symlink also raises ENOTDIR, so the two are
    told apart by an lstat before anyone is told their path was tampered
    with mid-command.
    """
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    with open(os.path.join(rootfs, "file"), "w") as fh:
        fh.write("x")

    with pytest.raises(SystemExit) as exc:
        with paths.pin_path("box:/file/below",
                            os.path.join(rootfs, "file", "below")):
            pass
    assert exc.value.code == 1
    err = capsys.readouterr().err
    assert "changed while it was being resolved" not in err
    assert "Not a directory" in err


def test_pin_path_still_reports_a_symlink_component_as_a_race(builders,
                                                              capsys):
    builders.make_container("box")
    rootfs = paths.container_rootfs("box")
    os.mkdir(os.path.join(rootfs, "real"))
    os.symlink("real", os.path.join(rootfs, "swapped"))

    with pytest.raises(SystemExit) as exc:
        with paths.pin_path("box:/swapped/x",
                            os.path.join(rootfs, "swapped", "x")):
            pass
    assert exc.value.code == 1
    assert "changed while it was being resolved" in capsys.readouterr().err


# ----- container_locks_for_spec_pair --------------------------------------

def _summarise(locks):
    return [(lk._display, lk._exclusive) for lk in locks]


def test_locks_same_container_single_exclusive():
    locks = paths.container_locks_for_spec_pair("box:/a", "box:/b", "copy")
    assert _summarise(locks) == [("box", True)]


def test_locks_two_containers_sorted_dst_exclusive():
    locks = paths.container_locks_for_spec_pair("src:/a", "dst:/b", "copy")
    # Sorted by name: dst, src. dst is exclusive, src shared.
    assert _summarise(locks) == [("dst", True), ("src", False)]


def test_locks_dst_only():
    locks = paths.container_locks_for_spec_pair("/host/path", "dst:/b", "copy")
    assert _summarise(locks) == [("dst", True)]


def test_locks_src_only():
    locks = paths.container_locks_for_spec_pair("src:/a", "/host/path", "copy")
    assert _summarise(locks) == [("src", False)]


def test_locks_neither():
    assert paths.container_locks_for_spec_pair("/a", "/b", "copy") == []


# ----- a colon only separates when nothing before it is a path ------------

def test_container_from_spec_ignores_a_colon_in_a_host_path():
    """scp's rule: a '/' before the colon means the whole thing is a path.

    Without it a host file with a colon in its name could not be addressed
    at all — the prefix was taken for a container name and rejected as
    invalid, with no spelling available to say otherwise.
    """
    assert paths.container_from_spec("/tmp/a:b") is None
    assert paths.container_from_spec("./a:b") is None
    assert paths.container_from_spec("dir/a:b") is None
    # A bare name still separates, so `./` is how a local `a:b` is spelled.
    assert paths.container_from_spec("a:b") == "a"
    assert paths.container_from_spec("box:/etc/hosts") == "box"


def test_resolve_host_path_with_a_colon_in_its_name(tmp_path, monkeypatch):
    target = tmp_path / "odd:name"
    target.write_text("x")
    monkeypatch.chdir(tmp_path)
    assert paths.resolve_container_path(str(target)) == str(target)
    assert paths.resolve_container_path("./odd:name") == str(target)


def test_locks_none_for_host_paths_holding_colons():
    assert paths.container_locks_for_spec_pair("/a:1", "/b:2", "copy") == []
