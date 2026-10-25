# Tests for the Termux baseline of a normal-type login environment
# (proot_distro.commands.login._build_normal_env) and its way into the
# /etc/profile.d snippet that `su -` re-sources.
#
# The test host is not Termux, so the module's IS_TERMUX is patched; the
# builder reads nothing else that is host-specific apart from the Android
# system vars, which are cleared so the assertions do not depend on the
# environment the suite runs in.

import os

import pytest

from proot_distro.commands import login
from proot_distro.commands.login import env as login_env
from proot_distro.paths import container_rootfs


BASELINE = {
    "MOZ_FAKE_NO_SANDBOX": "1",
    "MOZ_SHM_NO_SEALS": "1",
    "PULSE_SERVER": "127.0.0.1",
}


@pytest.fixture
def termux_host(monkeypatch):
    monkeypatch.setattr(login, "IS_TERMUX", True)
    for var in login_env.ANDROID_HOST_ENV_VARS:
        monkeypatch.delenv(var, raising=False)


def _env(extra_env=(), *, minimal=False, isolated=False):
    return login._build_normal_env(
        "box", "root", "/root", list(extra_env), minimal, isolated,
    )


@pytest.mark.parametrize("isolated", [False, True])
def test_termux_session_gets_the_baseline(builders, termux_host, isolated):
    builders.make_container("box")
    env = _env(isolated=isolated)
    for key, val in BASELINE.items():
        assert env[key] == val


def test_minimal_session_has_no_baseline(builders, termux_host):
    builders.make_container("box")
    env = _env(minimal=True)
    for key in BASELINE:
        assert key not in env


def test_baseline_is_termux_only(builders):
    builders.make_container("box")
    env = _env()
    for key in BASELINE:
        assert key not in env


def test_image_env_cannot_override_the_baseline(builders, termux_host):
    builders.make_container("box", manifest=builders.simple_image_manifest(
        env=[f"{key}=0" for key in BASELINE] + ["LANG=C.UTF-8"],
    ))
    env = _env()
    for key, val in BASELINE.items():
        assert key in login_env.IMAGE_ENV_BLOCKED
        assert env[key] == val
    assert env["LANG"] == "C.UTF-8"


def test_user_env_overrides_the_baseline(builders, termux_host):
    builders.make_container("box")
    env = _env([f"{key}=0" for key in BASELINE])
    for key in BASELINE:
        assert env[key] == "0"


def test_baseline_survives_into_the_profile_snippet(builders, termux_host):
    builders.make_container("box")
    # What login hands inject_termux_profile is the dict this builds.
    rootfs = container_rootfs("box")
    profile_d = os.path.join(rootfs, "etc", "profile.d")
    os.makedirs(profile_d, exist_ok=True)
    login_env.inject_termux_profile(rootfs, _env())
    with open(os.path.join(profile_d, "termux-profile.sh")) as fh:
        content = fh.read()
    for key, val in BASELINE.items():
        assert f"export {key}='{val}'" in content
