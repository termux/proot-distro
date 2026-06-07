# Tests for proot_distro.helpers.build_engine.users — user/group resolution
# against the rootfs's own /etc/passwd and /etc/group.

from proot_distro.helpers.build_engine import users


def _rootfs(tmp_path, builders):
    root = tmp_path / "rootfs"
    builders.make_rootfs(str(root))
    return str(root)


def test_resolve_id_numeric_passthrough(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    assert users.resolve_id(root, "1234", is_group=False, default=0) == 1234


def test_resolve_id_user_name(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    assert users.resolve_id(root, "root", is_group=False, default=99) == 0
    assert users.resolve_id(root, "tester", is_group=False, default=99) == 1000


def test_resolve_id_group_name(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    assert users.resolve_id(root, "staff", is_group=True, default=99) == 50


def test_resolve_id_unknown_returns_default(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    assert users.resolve_id(root, "ghost", is_group=False, default=42) == 42


def test_resolve_id_empty_returns_default(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    assert users.resolve_id(root, "", is_group=False, default=7) == 7


def test_resolve_id_missing_passwd_returns_default(tmp_path, builders):
    root = tmp_path / "bare"
    root.mkdir()
    assert users.resolve_id(str(root), "root", is_group=False, default=5) == 5


def test_resolve_chown_user_and_group(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    assert users.resolve_chown(root, "tester:staff") == (1000, 50)


def test_resolve_chown_user_only_group_defaults_to_uid(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    assert users.resolve_chown(root, "tester") == (1000, 1000)


def test_resolve_chown_numeric(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    assert users.resolve_chown(root, "5:9") == (5, 9)


def test_resolve_user_for_proot(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    assert users.resolve_user_for_proot(root, "tester") == (1000, 1000)
    assert users.resolve_user_for_proot(root, "") == (0, 0)
    assert users.resolve_user_for_proot(root, "root:root") == (0, 0)
    assert users.resolve_user_for_proot(root, "tester:staff") == (1000, 50)
