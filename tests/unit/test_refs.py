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


# ----- the grammar: Docker's reference format -------------------------------
#
# The splitting that came before parse_reference() never refused anything,
# only misunderstood it: `ubuntu#x` and `ubuntu&scope=...` went into the
# registry URL and the token request as written, `ubuntu:` asked for
# `/manifests/`, `ubuntu@sha256:<hex>` was the repository
# `library/ubuntu@sha256`, and `build -t localhost:5000/app` was refused.

_HEX = "0123456789abcdef" * 4


@pytest.mark.parametrize("ref,expected", [
    ("ubuntu", ("", "library/ubuntu", "latest", "")),
    ("ubuntu:24.04", ("", "library/ubuntu", "24.04", "")),
    ("docker.io/ubuntu", ("", "library/ubuntu", "latest", "")),
    ("index.docker.io/myuser/img:1.0", ("", "myuser/img", "1.0", "")),
    ("ghcr.io/foo/bar", ("ghcr.io", "foo/bar", "latest", "")),
    ("localhost:5000/app", ("localhost:5000", "app", "latest", "")),
    # `localhost` and an upper-case first component name a registry, as
    # they do for Docker: a repository path can hold neither.
    ("localhost/app", ("localhost", "app", "latest", "")),
    ("Registry/app", ("Registry", "app", "latest", "")),
    ("[::1]:5000/app:v1", ("[::1]:5000", "app", "v1", "")),
    ("a.b-c/x__y/z.w-v:T_1.0-rc", ("a.b-c", "x__y/z.w-v", "T_1.0-rc", "")),
    (f"ubuntu@sha256:{_HEX}", ("", "library/ubuntu", "", f"sha256:{_HEX}")),
    (f"debian:12@sha256:{_HEX}",
     ("", "library/debian", "12", f"sha256:{_HEX}")),
    (f"ghcr.io/foo/bar@sha256:{_HEX}",
     ("ghcr.io", "foo/bar", "", f"sha256:{_HEX}")),
])
def test_parse_reference(ref, expected):
    assert tuple(refs.parse_reference(ref)) == expected


@pytest.mark.parametrize("ref,reason", [
    ("", "empty"),
    ("Ubuntu", "must be lowercase"),
    ("localhost:5000/App", "must be lowercase"),
    ("ubuntu:", "invalid reference format"),
    (":latest", "invalid reference format"),
    ("a//b", "invalid reference format"),
    ("app_", "invalid reference format"),
    ("a___b", "invalid reference format"),
    ("ubuntu?x=1", "invalid reference format"),
    ("ubuntu#frag", "invalid reference format"),
    ("ubuntu&scope=repository:x:push", "invalid reference format"),
    ("ubuntu\n", "invalid reference format"),
    ("ubuntu:" + "t" * 129, "invalid reference format"),
    ("../../x:1", "registry host is not valid"),
    ("foo:1.0/bar", "registry host is not valid"),
    ("[:5000/img", "registry host is not valid"),
    ("reg\udcff.example/img", "registry host is not valid"),
    ("localhost:99999/app", "port is out of range"),
    (_HEX, "image ID"),
    ("x" * 250 + "/" + "y" * 10, "255 characters"),
    ("x" * 2000, "longer than"),
    (f"ubuntu@sha512:{_HEX * 2}", "unsupported digest algorithm 'sha512'"),
    (f"ubuntu@sha256:{_HEX.upper()}", "64 lowercase"),
    (f"ubuntu@sha256:{_HEX[:40]}", "invalid digest"),
    (f"ubuntu@{_HEX}", "invalid reference format"),
])
def test_parse_reference_refuses(ref, reason):
    with pytest.raises(refs.InvalidReference, match=reason):
        refs.parse_reference(ref)


def test_invalid_reference_is_a_runtime_error():
    # What pull_image promises its callers; FROM and COPY --from catch it.
    assert issubclass(refs.InvalidReference, RuntimeError)


def test_parse_tag_reference_refuses_a_digest():
    assert refs.parse_tag_reference("localhost:5000/app").repo == "app"
    with pytest.raises(refs.InvalidReference, match="use a tag"):
        refs.parse_tag_reference(f"app@sha256:{_HEX}")


@pytest.mark.parametrize("ref,canonical", [
    # A pin is the image; the tag beside it does not change which.
    (f"debian:12@sha256:{_HEX}", f"library/debian@sha256:{_HEX}"),
    (f"docker.io/library/debian@sha256:{_HEX}",
     f"library/debian@sha256:{_HEX}"),
    # The same string the loose split gave, so no cache key moves.
    ("localhost/app", "localhost/app:latest"),
    ("Registry/app", "Registry/app:latest"),
])
def test_canonical_ref_of_newly_understood_references(ref, canonical):
    assert refs.canonical_ref(ref) == canonical


@pytest.mark.parametrize("ref", [
    "Ubuntu", "ubuntu:", "a//b", "../../x:1", _HEX, "reg\udcff.x/img", "",
])
def test_total_functions_never_raise(ref):
    # A cache key or a lookup is also asked about strings nothing
    # validated -- a container's manifest.json, a `remove --image` ID.
    refs.canonical_ref(ref)
    refs.parse_image_ref(ref)
    refs.with_explicit_tag(ref)
    refs.derive_alias(ref)


def test_parse_image_ref_follows_the_grammar_for_a_valid_reference():
    assert refs.parse_image_ref("localhost/app:1") == ("localhost", "app", "1")
    assert refs.parse_image_ref(f"ubuntu@sha256:{_HEX}") == (
        "", "library/ubuntu", "")


@pytest.mark.parametrize("ref,alias", [
    (f"debian:12@sha256:{_HEX}", "debian"),
    (f"ghcr.io/foo/bar@sha256:{_HEX}", "bar"),
    ("localhost/app", "app"),
])
def test_derive_alias_of_newly_understood_references(ref, alias):
    assert refs.derive_alias(ref) == alias


def test_with_explicit_tag_leaves_a_pin_alone():
    ref = f"ubuntu@sha256:{_HEX}"
    assert refs.with_explicit_tag(ref) == ref


@pytest.mark.parametrize("name,ok", [
    ("library/ubuntu", True), ("my-user/my_image", True), ("a.b/c__d", True),
    ("Ubuntu", False), ("nginx\n", False), ("a/", False), ("a" * 256, False),
    (None, False),
])
def test_is_repository_path(name, ok):
    assert refs.is_repository_path(name) is ok
