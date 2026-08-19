# Containment tests for proot_distro.helpers.rootfs.
#
# These fixups run against a rootfs that was just unpacked from an image
# the user named but did not write, and they run on the *host* side, before
# anything in the rootfs is executed. Every entry they touch is therefore
# attacker-chosen content, and both operations involved — a chmod and an
# open — follow symlinks unless stopped.

import os
import stat

import pytest

from proot_distro.helpers.rootfs import (
    register_android_ids, write_hosts, write_resolv_conf,
)


@pytest.fixture
def env(tmp_path):
    root = tmp_path / "rootfs"
    (root / "etc").mkdir(parents=True)
    victim = tmp_path / "id_rsa"
    victim.write_text("PRIVATE KEY\n")
    victim.chmod(0o600)
    # register_android_ids is only reached when etc/passwd is a real file.
    (root / "etc" / "passwd").write_text("root:x:0:0::/root:/bin/sh\n")
    return str(root), victim


@pytest.mark.parametrize("name", ["passwd", "shadow", "group", "gshadow"])
def test_id_file_symlink_does_not_reach_the_host(env, name):
    root, victim = env
    link = os.path.join(root, "etc", name)
    if os.path.exists(link):
        os.remove(link)
    os.symlink(str(victim), link)

    register_android_ids(root)

    # Neither the chmod nor the append followed the link.
    assert victim.read_text() == "PRIVATE KEY\n"
    assert stat.S_IMODE(victim.stat().st_mode) == 0o600
    # The link itself is left where it is, not replaced or written through.
    assert os.path.islink(link)


def test_etc_symlink_does_not_redirect_any_write(env, tmp_path):
    # `etc` itself as a symlink aims every write in the module at a host
    # directory. os.path.isdir() at the call site follows it, so the guard
    # there is no help.
    root, _victim = env
    outside = tmp_path / "outside"
    outside.mkdir()
    marker = outside / "keep"
    marker.write_text("KEEP")

    import shutil
    shutil.rmtree(os.path.join(root, "etc"))
    os.symlink(str(outside), os.path.join(root, "etc"))

    write_resolv_conf(root)
    write_hosts(root)
    register_android_ids(root)

    assert sorted(os.listdir(str(outside))) == ["keep"]
    assert marker.read_text() == "KEEP"


def test_fifo_under_an_id_file_name_is_refused(env):
    # Opening a FIFO for append blocks until a reader appears, which a
    # hostile image simply never supplies.
    root, _victim = env
    path = os.path.join(root, "etc", "group")
    os.mkfifo(path)

    register_android_ids(root)

    assert stat.S_ISFIFO(os.lstat(path).st_mode)


@pytest.mark.parametrize("writer,name", [
    (write_resolv_conf, "resolv.conf"),
    (write_hosts, "hosts"),
])
def test_replaced_file_symlink_is_unlinked_not_followed(env, writer, name):
    root, victim = env
    link = os.path.join(root, "etc", name)
    os.symlink(str(victim), link)

    writer(root)

    assert victim.read_text() == "PRIVATE KEY\n"
    assert not os.path.islink(link)
    assert os.path.isfile(link)


# --- behaviour that must survive the hardening -----------------------------

def test_normal_image_still_gets_its_fixups(env):
    root, _victim = env
    etc = os.path.join(root, "etc")

    write_resolv_conf(root)
    write_hosts(root)
    register_android_ids(root)

    assert "nameserver" in open(os.path.join(etc, "resolv.conf")).read()
    assert "127.0.0.1" in open(os.path.join(etc, "hosts")).read()
    passwd = open(os.path.join(etc, "passwd")).read()
    assert passwd.startswith("root:x:0:0")          # image content kept
    assert "\naid_" in passwd                        # our line appended
    # shadow and group are created when absent, as open(path, "a") did;
    # gshadow was always guarded by an exists check and still is.
    assert os.path.isfile(os.path.join(etc, "shadow"))
    assert os.path.isfile(os.path.join(etc, "group"))
    assert not os.path.exists(os.path.join(etc, "gshadow"))


def test_existing_gshadow_is_appended_to(env):
    root, _victim = env
    etc = os.path.join(root, "etc")
    open(os.path.join(etc, "gshadow"), "w").write("root:*::\n")

    register_android_ids(root)

    body = open(os.path.join(etc, "gshadow")).read()
    assert body.startswith("root:*::")
    assert "\naid_" in body


def test_missing_etc_is_a_no_op(tmp_path):
    root = tmp_path / "rootfs"
    root.mkdir()
    write_resolv_conf(str(root))
    write_hosts(str(root))
    register_android_ids(str(root))
    assert os.listdir(str(root)) == []
