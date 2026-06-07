# Tests for proot_distro.commands.login.env — image Env harvesting and the
# /etc/profile.d snippet injector.

import os

from proot_distro.commands.login import env as login_env
from proot_distro.paths import container_dir


def test_read_manifest_env_filters(builders):
    manifest = {
        "image_config": {
            "config": {
                "Env": ["A=B", "NOEQUALS", "C=D", 123, "E=with=eq"],
            }
        }
    }
    builders.make_container("box", manifest=manifest)
    got = login_env.read_manifest_env(container_dir("box"))
    assert got == ["A=B", "C=D", "E=with=eq"]


def test_read_manifest_env_missing(builders):
    builders.make_container("box")  # no manifest
    assert login_env.read_manifest_env(container_dir("box")) == []


def _profile_dir(tmp_path):
    root = tmp_path / "rootfs"
    (root / "etc" / "profile.d").mkdir(parents=True)
    return root


def test_inject_profile_path_guard(tmp_path):
    root = _profile_dir(tmp_path)
    login_env.inject_termux_profile(str(root), {"PATH": "/bin"})
    content = (root / "etc" / "profile.d" / "termux-profile.sh").read_text()
    assert 'case ":${PATH}:" in' in content
    assert "export PATH=" in content


def test_inject_profile_exports_normal_key(tmp_path):
    root = _profile_dir(tmp_path)
    login_env.inject_termux_profile(str(root), {"FOO": "bar"})
    content = (root / "etc" / "profile.d" / "termux-profile.sh").read_text()
    assert "export FOO='bar'" in content


def test_inject_profile_single_quote_idiom(tmp_path):
    root = _profile_dir(tmp_path)
    login_env.inject_termux_profile(str(root), {"Q": "a'b"})
    content = (root / "etc" / "profile.d" / "termux-profile.sh").read_text()
    # Standard close/escape/reopen idiom.
    assert r"export Q='a'\''b'" in content


def test_inject_profile_drops_malformed_key(tmp_path):
    root = _profile_dir(tmp_path)
    login_env.inject_termux_profile(
        str(root), {"BAD KEY": "x", "EVIL;rm": "y", "GOOD": "z"}
    )
    content = (root / "etc" / "profile.d" / "termux-profile.sh").read_text()
    assert "BAD KEY" not in content
    assert "EVIL;rm" not in content
    assert "export GOOD='z'" in content


def test_inject_profile_skips_session_and_proot_vars(tmp_path):
    root = _profile_dir(tmp_path)
    login_env.inject_termux_profile(str(root), {
        "HOME": "/root", "USER": "root", "TERM": "xterm",
        "PROOT_L2S_DIR": "/x", "LD_PRELOAD": "/y", "KEEP": "v",
    })
    content = (root / "etc" / "profile.d" / "termux-profile.sh").read_text()
    assert "export HOME=" not in content
    assert "export USER=" not in content
    assert "PROOT_L2S_DIR" not in content
    assert "LD_PRELOAD" not in content
    assert "export KEEP='v'" in content


def test_inject_profile_removes_legacy_snippet(tmp_path):
    root = _profile_dir(tmp_path)
    legacy = root / "etc" / "profile.d" / "termux-prefix.sh"
    legacy.write_text("# stale\n")
    login_env.inject_termux_profile(str(root), {"FOO": "bar"})
    assert not legacy.exists()


def test_inject_profile_noop_without_profile_d(tmp_path):
    root = tmp_path / "rootfs"
    (root / "etc").mkdir(parents=True)  # no profile.d
    # Should silently do nothing (no crash, no file).
    login_env.inject_termux_profile(str(root), {"FOO": "bar"})
    assert not (root / "etc" / "profile.d").exists()


def test_image_env_blocked_membership():
    assert "TERM" in login_env.IMAGE_ENV_BLOCKED
    assert "COLORTERM" in login_env.IMAGE_ENV_BLOCKED
    assert "ANDROID_ROOT" in login_env.IMAGE_ENV_BLOCKED
    assert "FOO" not in login_env.IMAGE_ENV_BLOCKED
