# Tests for proot_distro.helpers.build_engine.dockerignore — pattern matching
# used to filter the build context.

from proot_distro.helpers.build_engine import dockerignore as di


def test_no_patterns_never_ignores():
    assert di.is_ignored("anything", []) is False


def test_exact_and_glob():
    pats = ["*.log"]
    assert di.is_ignored("a.log", pats) is True
    assert di.is_ignored("a.txt", pats) is False


def test_prefix_directory_match():
    pats = ["node_modules"]
    assert di.is_ignored("node_modules", pats) is True
    assert di.is_ignored("node_modules/pkg/index.js", pats) is True


def test_double_star():
    pats = ["**/foo"]
    assert di.is_ignored("a/foo", pats) is True
    assert di.is_ignored("a/b/foo", pats) is True


def test_negation_reinclude():
    pats = ["*", "!keep.txt"]
    assert di.is_ignored("keep.txt", pats) is False
    assert di.is_ignored("drop.bin", pats) is True


def test_negation_order_matters():
    # Later patterns win: re-include then re-exclude.
    pats = ["secret", "!secret", "secret"]
    assert di.is_ignored("secret", pats) is True


def test_dockerfile_and_dockerignore_never_ignored():
    pats = ["*"]
    assert di.is_ignored("Dockerfile", pats) is False
    assert di.is_ignored(".dockerignore", pats) is False


def test_load_dockerignore(tmp_path):
    (tmp_path / ".dockerignore").write_text(
        "# comment\n\n*.log\nnode_modules\n!keep.log\n"
    )
    pats = di.load_dockerignore(str(tmp_path))
    assert pats == ["*.log", "node_modules", "!keep.log"]


def test_load_dockerignore_missing(tmp_path):
    assert di.load_dockerignore(str(tmp_path)) == []


def test_simple_glob(tmp_path):
    (tmp_path / "a.txt").write_text("x")
    (tmp_path / "b.txt").write_text("y")
    (tmp_path / "c.md").write_text("z")
    got = sorted(di.simple_glob(str(tmp_path), "*.txt"))
    assert got == ["a.txt", "b.txt"]
