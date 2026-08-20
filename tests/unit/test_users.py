# Tests for proot_distro.helpers.build_engine.users — user/group resolution
# against the rootfs's own /etc/passwd and /etc/group.

import os
import shutil

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


# --- containment: /etc/passwd is image content, and so is the path to it ---

def test_etc_symlinked_out_of_the_rootfs_reads_nothing(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    outside = tmp_path / "outside"
    (outside / "etc").mkdir(parents=True)
    (outside / "etc" / "passwd").write_text("intruder:x:1337:1337::/:/bin/sh\n")

    shutil.rmtree(os.path.join(root, "etc"))
    os.symlink(str(outside / "etc"), os.path.join(root, "etc"))

    assert users.resolve_id(root, "intruder", is_group=False, default=7) == 7


def test_passwd_symlinked_at_a_host_file_reads_nothing(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    victim = tmp_path / "host_passwd"
    victim.write_text("intruder:x:1337:1337::/:/bin/sh\n")

    passwd = os.path.join(root, "etc", "passwd")
    os.remove(passwd)
    os.symlink(str(victim), passwd)

    assert users.resolve_id(root, "intruder", is_group=False, default=7) == 7


def test_passwd_symlink_is_re_rooted_inside_the_rootfs(tmp_path, builders):
    # The Nix case: /etc/passwd points at an absolute path that only exists
    # inside the guest. The link is followed, just anchored at the rootfs.
    root = _rootfs(tmp_path, builders)
    store = os.path.join(root, "nix", "store")
    os.makedirs(store)
    with open(os.path.join(store, "passwd"), "w") as fh:
        fh.write("nixuser:x:2000:2000::/:/bin/sh\n")

    passwd = os.path.join(root, "etc", "passwd")
    os.remove(passwd)
    os.symlink("/nix/store/passwd", passwd)

    assert users.resolve_id(root, "nixuser", is_group=False, default=7) == 2000


def test_dotdot_in_a_symlink_target_clamps_at_the_rootfs(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    victim = tmp_path / "host_passwd"
    victim.write_text("intruder:x:1337:1337::/:/bin/sh\n")

    passwd = os.path.join(root, "etc", "passwd")
    os.remove(passwd)
    # <root>/etc/../../host_passwd is the victim, as the host resolves it.
    os.symlink("../../host_passwd", passwd)

    assert users.resolve_id(root, "intruder", is_group=False, default=7) == 7


def test_symlink_loop_gives_up(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    passwd = os.path.join(root, "etc", "passwd")
    os.remove(passwd)
    os.symlink("/etc/passwd2", passwd)
    os.symlink("/etc/passwd", os.path.join(root, "etc", "passwd2"))

    assert users.resolve_id(root, "root", is_group=False, default=7) == 7


def test_a_fifo_named_passwd_does_not_block(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    passwd = os.path.join(root, "etc", "passwd")
    os.remove(passwd)
    os.mkfifo(passwd)

    assert users.resolve_id(root, "root", is_group=False, default=7) == 7


def test_an_enormous_passwd_is_read_only_up_to_the_cap(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    passwd = os.path.join(root, "etc", "passwd")
    with open(passwd, "w") as fh:
        fh.write("root:x:0:0::/root:/bin/sh\n")
        fh.write("x" * (users._MAX_ID_FILE_BYTES * 2))

    # The entry before the padding still resolves; the padding never
    # becomes a single multi-megabyte line in memory.
    assert users.resolve_id(root, "root", is_group=False, default=7) == 0


def test_undecodable_passwd_does_not_raise(tmp_path, builders):
    root = _rootfs(tmp_path, builders)
    passwd = os.path.join(root, "etc", "passwd")
    with open(passwd, "wb") as fh:
        fh.write(b"root:x:0:0::/root:/bin/sh\n\xff\xfe:x:1:1::/:/bin/sh\n")

    assert users.resolve_id(root, "root", is_group=False, default=7) == 0
