# Tests for proot_distro.commands.login.env — image Env harvesting and the
# /etc/profile.d snippet injector.

import os
import shutil

import pytest

from _builders import simple_image_manifest
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


def test_image_env_pairs_drop_what_cannot_be_exported(builders):
    # os.execvpe() raises ValueError on a NUL, UnicodeEncodeError on a
    # lone surrogate and E2BIG on a string past MAX_ARG_STRLEN, and
    # nothing in login caught any of them; the file is a guest's.
    builders.make_container("box", manifest=builders.simple_image_manifest(
        env=["A=x\u0000y", "B\u0000C=z", "S=\ud800", "BIG=" + "x" * 200_000,
             "OK=1"],
    ))
    assert list(login_env.image_env_pairs("box")) == [("OK", "1")]


# --- identity_env_pairs ------------------------------------------------------

_DIGEST = "sha256:" + "ab" * 32


def _identity_manifest(image_ref="test:latest", digest=_DIGEST):
    manifest = simple_image_manifest(image_ref=image_ref)
    if digest is not None:
        manifest["manifest"]["config"] = {"digest": digest}
    return manifest


def test_identity_pairs_full(builders):
    builders.make_container("box", manifest=_identity_manifest())
    assert dict(login_env.identity_env_pairs("box")) == {
        "PD_CONTAINER": "box",
        "container": "proot-distro",
        "PD_IMAGE": "test:latest",
        "PD_IMAGE_ID": _DIGEST,
    }


def test_identity_pairs_without_a_manifest(builders):
    # A plain-tarball container: the name and the marker are still
    # answered, the image is simply unknown.
    builders.make_container("box")
    assert dict(login_env.identity_env_pairs("box")) == {
        "PD_CONTAINER": "box",
        "container": "proot-distro",
    }


def test_identity_pairs_image_ref_verbatim(builders):
    # Not normalised: the recorded reference may be a URL, which
    # with_explicit_tag() would turn into `.../x.tar:latest`.
    builders.make_container("box", manifest=_identity_manifest(
        image_ref="https://example.org/rootfs.tar.xz",
    ))
    got = dict(login_env.identity_env_pairs("box"))
    assert got["PD_IMAGE"] == "https://example.org/rootfs.tar.xz"


@pytest.mark.parametrize("ref", ["", 7, None, "a\u0000b", "\ud800",
                                 "x" * 200_000])
def test_identity_pairs_unusable_image_ref(builders, ref):
    builders.make_container("box", manifest=_identity_manifest(image_ref=ref))
    got = dict(login_env.identity_env_pairs("box"))
    assert "PD_IMAGE" not in got
    assert got["PD_IMAGE_ID"] == _DIGEST     # each field on its own


@pytest.mark.parametrize("digest", [
    None,                    # no config descriptor at all
    7,                       # not a string
    "../x:y",                # not a digest
    "sha256",                # no hex half
    "sha256:zz",             # not hex
])
def test_identity_pairs_unusable_image_id(builders, digest):
    builders.make_container("box", manifest=_identity_manifest(digest=digest))
    got = dict(login_env.identity_env_pairs("box"))
    assert "PD_IMAGE_ID" not in got
    assert got["PD_IMAGE"] == "test:latest"


def test_identity_pairs_manifest_of_the_wrong_shape(builders):
    for manifest in ({"manifest": "x"}, {"manifest": {"config": "x"}},
                     {"manifest": {"config": {}}}, {"image_ref": ["a"]}):
        builders.make_container("box", manifest=manifest)
        assert dict(login_env.identity_env_pairs("box")) == {
            "PD_CONTAINER": "box", "container": "proot-distro",
        }
        shutil.rmtree(container_dir("box"))


def test_identity_keys_are_blocked_from_the_image_env():
    for key in login_env.IDENTITY_ENV_KEYS:
        assert key in login_env.IMAGE_ENV_BLOCKED


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


def test_inject_profile_path_block_is_mode_bound(tmp_path):
    # The PATH append names the Termux prefix, which is bound into the
    # guest only in the default mode on Termux; elsewhere the block
    # would re-export a directory the guest cannot see.
    root = _profile_dir(tmp_path)
    login_env.inject_termux_profile(str(root), {"FOO": "bar"},
                                    termux_path=False)
    content = (root / "etc" / "profile.d" / "termux-profile.sh").read_text()
    assert "PATH" not in content
    assert "export FOO='bar'" in content


def test_inject_profile_exports_normal_key(tmp_path):
    root = _profile_dir(tmp_path)
    login_env.inject_termux_profile(str(root), {"FOO": "bar"})
    content = (root / "etc" / "profile.d" / "termux-profile.sh").read_text()
    assert "export FOO='bar'" in content


def test_inject_profile_exports_identity_keys(tmp_path):
    # `su - user` re-sources /etc/profile; the identity of the container
    # must survive that like every other proot-distro-set variable.
    root = _profile_dir(tmp_path)
    login_env.inject_termux_profile(str(root), {
        "PD_CONTAINER": "box", "container": "proot-distro",
        "PD_IMAGE": "debian:bookworm", "PD_IMAGE_ID": "sha256:" + "0" * 64,
    })
    content = (root / "etc" / "profile.d" / "termux-profile.sh").read_text()
    assert "export PD_CONTAINER='box'" in content
    assert "export container='proot-distro'" in content
    assert "export PD_IMAGE='debian:bookworm'" in content
    assert "export PD_IMAGE_ID='sha256:" in content


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
