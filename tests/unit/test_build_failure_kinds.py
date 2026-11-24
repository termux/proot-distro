"""Build failures keep the kind of the error that caused them."""

from types import SimpleNamespace

import pytest

from proot_distro.helpers.build_engine.errors import BuildError
from proot_distro.helpers.docker.transport import RegistryAuthenticationError
from proot_distro.operation_errors import (
    IntegrityFailure,
    NetworkFailure,
    OperationFailure,
)
from proot_distro.operations.errors import (
    BuildAuthenticationFailure,
    BuildIntegrityFailure,
    BuildNetworkFailure,
    build_failure,
)


def _engine_error(cause):
    """A BuildError the way the engine raises one: ``from`` the original."""
    try:
        try:
            raise cause
        except RuntimeError as inner:
            raise BuildError(f"FROM base: {inner}") from inner
    except BuildError as exc:
        return exc


@pytest.mark.parametrize("cause,expected,kind", [
    (NetworkFailure("down"), BuildNetworkFailure, NetworkFailure),
    (RegistryAuthenticationError("denied"), BuildAuthenticationFailure,
     RegistryAuthenticationError),
    (IntegrityFailure("bad digest"), BuildIntegrityFailure,
     IntegrityFailure),
])
def test_build_failure_keeps_the_cause_kind(cause, expected, kind):
    """Regression: every unreachable base image looked like a generic error."""
    failure = build_failure(_engine_error(cause), "Build failed: x")

    assert type(failure) is expected
    # Still an OperationFailure, so the CLI adapter's handler is unchanged.
    assert isinstance(failure, OperationFailure)
    assert isinstance(failure, kind)
    assert str(failure) == "Build failed: x"


def test_build_failure_looks_through_a_chain_of_causes():
    try:
        try:
            raise NetworkFailure("down")
        except NetworkFailure as first:
            try:
                raise RuntimeError("wrapped") from first
            except RuntimeError as second:
                raise BuildError("outer") from second
    except BuildError as exc:
        failure = build_failure(exc, "Build failed: outer")

    assert isinstance(failure, BuildNetworkFailure)


@pytest.mark.parametrize("make", [
    lambda: BuildError("Dockerfile contains no FROM instruction."),
    lambda: _engine_error(RuntimeError("some other registry problem")),
    lambda: OSError("disk full"),
])
def test_build_failure_without_a_typed_cause_stays_generic(make):
    failure = build_failure(make(), "Build failed: x")

    assert type(failure) is OperationFailure
    assert not isinstance(failure, (NetworkFailure, IntegrityFailure))


def test_cli_build_handles_typed_failures_without_a_traceback(
    monkeypatch, capsys,
):
    """The CLI must treat the new subclasses as ordinary build failures."""
    from proot_distro.commands import build as command

    for failure in (
        BuildNetworkFailure("Build failed: network"),
        BuildAuthenticationFailure("Build failed: denied"),
        BuildIntegrityFailure("Build failed: digest"),
    ):
        def fail(*_args, _failure=failure, **_kwargs):
            raise _failure

        monkeypatch.setattr(command, "build_image", fail)
        args = SimpleNamespace(path=".", tags=["x/y:1"])

        with pytest.raises(SystemExit) as exc:
            command.command_build(args)

        assert exc.value.code == 1
        assert str(failure) in capsys.readouterr().err
