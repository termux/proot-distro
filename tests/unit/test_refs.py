# Tests for proot_distro.helpers.docker.refs — image reference parsing and
# local alias derivation.

import pytest

from proot_distro.helpers.docker import refs


@pytest.mark.parametrize("ref,expected", [
    ("ubuntu", ("", "library/ubuntu", "latest")),
    ("ubuntu:24.04", ("", "library/ubuntu", "24.04")),
    ("myuser/img:1.0", ("", "myuser/img", "1.0")),
    ("docker.io/library/ubuntu:24.04", ("", "library/ubuntu", "24.04")),
    ("index.docker.io/library/ubuntu", ("", "library/ubuntu", "latest")),
    ("ghcr.io/foo/bar:latest", ("ghcr.io", "foo/bar", "latest")),
    ("localhost:5000/foo:tag", ("localhost:5000", "foo", "tag")),
    ("localhost:5000/foo", ("localhost:5000", "foo", "latest")),
    ("registry.example.com/team/app:v2", ("registry.example.com", "team/app", "v2")),
])
def test_parse_image_ref(ref, expected):
    assert refs.parse_image_ref(ref) == expected


@pytest.mark.parametrize("ref,alias", [
    ("ubuntu:24.04", "ubuntu"),
    ("myuser/img:tag", "img"),
    ("ghcr.io/foo/bar:tag", "bar"),
    ("localhost:5000/foo:tag", "foo"),
    ("ubuntu", "ubuntu"),
])
def test_derive_alias(ref, alias):
    assert refs.derive_alias(ref) == alias


def test_arch_to_docker_table():
    assert refs.ARCH_TO_DOCKER["aarch64"] == ("arm64", "")
    assert refs.ARCH_TO_DOCKER["arm"] == ("arm", "v7")
    assert refs.ARCH_TO_DOCKER["x86_64"] == ("amd64", "")
