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


@pytest.mark.parametrize("ref,canonical", [
    ("ubuntu", "library/ubuntu:latest"),
    ("ubuntu:24.04", "library/ubuntu:24.04"),
    ("docker.io/library/ubuntu:24.04", "library/ubuntu:24.04"),
    ("myuser/img", "myuser/img:latest"),
    ("ghcr.io/foo/bar", "ghcr.io/foo/bar:latest"),
    ("localhost:5000/foo", "localhost:5000/foo:latest"),
])
def test_canonical_ref(ref, canonical):
    assert refs.canonical_ref(ref) == canonical


@pytest.mark.parametrize("ref,tagged", [
    ("ubuntu", "ubuntu:latest"),
    ("ubuntu:24.04", "ubuntu:24.04"),
    ("ghcr.io/foo/bar", "ghcr.io/foo/bar:latest"),
    # A port in the registry host is not a tag.
    ("localhost:5000/foo", "localhost:5000/foo:latest"),
])
def test_with_explicit_tag(ref, tagged):
    assert refs.with_explicit_tag(ref) == tagged


def test_docker_to_arch_is_the_reverse_table():
    assert refs.DOCKER_TO_ARCH["arm64"] == "aarch64"
    assert refs.DOCKER_TO_ARCH["amd64"] == "x86_64"
    assert refs.DOCKER_TO_ARCH["386"] == "i686"
    for pd_arch, (docker, _variant) in refs.ARCH_TO_DOCKER.items():
        assert refs.DOCKER_TO_ARCH[docker] == pd_arch
