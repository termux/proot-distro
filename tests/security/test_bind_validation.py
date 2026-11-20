# Tests for proot_distro.session_command._add_custom_binds — the
# validation gate for user-supplied --bind specs.

import os

import pytest

from proot_distro.operations.errors import SessionValidationFailure
from proot_distro import session_command as proot_cmd


def test_empty_bind_rejected():
    with pytest.raises(SessionValidationFailure) as exc:
        proot_cmd._add_custom_binds([], [""])
    assert "cannot be empty" in str(exc.value)


def test_empty_source_rejected():
    with pytest.raises(SessionValidationFailure) as exc:
        proot_cmd._add_custom_binds([], [":/dest"])
    assert "source path cannot be empty" in str(exc.value)


def test_relative_destination_rejected():
    with pytest.raises(SessionValidationFailure) as exc:
        proot_cmd._add_custom_binds([], ["/src:relative/dest"])
    assert "must be an absolute path" in str(exc.value)


def test_valid_bind_appended_with_abspath(tmp_path):
    args = []
    src = tmp_path / "data"
    src.mkdir()
    proot_cmd._add_custom_binds(args, [f"{src}:/mnt/data"])
    assert f"--bind={os.path.abspath(str(src))}:/mnt/data" in args


def test_relative_source_is_abspathed(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel").mkdir()
    args = []
    proot_cmd._add_custom_binds(args, ["rel:/mnt/rel"])
    assert f"--bind={os.path.join(str(tmp_path), 'rel')}:/mnt/rel" in args


def test_overlapping_destination_warns_but_adds(capsys):
    args = ["--bind=/existing:/mnt/x"]
    proot_cmd._add_custom_binds(args, ["/other:/mnt/x"])
    err = capsys.readouterr().err
    assert "overlaps" in err
    # Still appended despite the overlap warning.
    assert any(a == "--bind=/other:/mnt/x" for a in args)


def test_source_only_bind(tmp_path):
    args = []
    src = tmp_path / "d"
    src.mkdir()
    proot_cmd._add_custom_binds(args, [str(src)])
    assert f"--bind={os.path.abspath(str(src))}" in args
