# proot self-test (proot_distro.prootcheck).
#
# The probe runs proot on the host's root with one --bind and asks a
# shell whether a file that exists only through the bind is there. The
# stand-in proot below parses --bind the way proot does and either
# confines (rewrites paths through the binds) or leaks (runs the command
# on the host as it is) -- the second is what Ubuntu 26.04's proot 5.4.0
# does in seccomp mode, where it stops on no system call at all.

import os
import subprocess

import pytest

from proot_distro import message, prootcheck

from _builders import fake_proot


# --- the probe itself -------------------------------------------------------

def test_probe_sees_the_bind_through_a_confining_proot(tmp_path,
                                                       real_proot_probe):
    assert prootcheck._probe(fake_proot(tmp_path, "confine"), {}) is True


def test_probe_sees_the_host_through_a_leaking_proot(tmp_path,
                                                     real_proot_probe):
    assert prootcheck._probe(fake_proot(tmp_path, "leak"), {}) is False


def test_probe_runs_proot_with_the_toggles_it_is_given(tmp_path,
                                                       real_proot_probe):
    proot = fake_proot(tmp_path, "leak-with-seccomp")

    assert prootcheck._probe(proot, {}) is False
    assert prootcheck._probe(proot, {"PROOT_NO_SECCOMP": "1"}) is True


def test_probe_reports_a_proot_that_fails_to_start(tmp_path,
                                                   real_proot_probe):
    verdict = prootcheck._probe(fake_proot(tmp_path, "fail"), {})

    assert isinstance(verdict, str)
    assert "status 1" in verdict


def test_probe_gives_up_on_a_proot_that_hangs(tmp_path, monkeypatch,
                                              real_proot_probe):
    monkeypatch.setattr(prootcheck, "_PROBE_TIMEOUT", 1)

    verdict = prootcheck._probe(fake_proot(tmp_path, "hang"), {})

    assert isinstance(verdict, str)
    assert "no answer" in verdict


def test_probe_without_its_shell_cannot_tell(tmp_path, monkeypatch,
                                             real_proot_probe):
    monkeypatch.setattr(prootcheck, "_PROBE_SHELL",
                        str(tmp_path / "no-such-shell"))

    verdict = prootcheck._probe(fake_proot(tmp_path, "confine"), {})

    assert verdict is not True and verdict is not False


def test_probe_of_a_missing_proot_cannot_tell(tmp_path, real_proot_probe):
    verdict = prootcheck._probe(str(tmp_path / "no-such-proot"), {})

    assert isinstance(verdict, str)
    assert "self-test failed" in verdict


def test_probe_leaves_nothing_behind(tmp_path, monkeypatch, real_proot_probe):
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.setattr(prootcheck.tempfile, "tempdir", str(scratch))

    for mode in ("confine", "leak", "fail"):
        prootcheck._probe(fake_proot(tmp_path, mode), {})

    assert os.listdir(str(scratch)) == []


def test_probe_hands_proot_only_path_and_the_toggles(monkeypatch,
                                                     real_proot_probe):
    # The probe runs the host's own shell, so nothing from the session's
    # environment -- an image's BASH_ENV, its PATH -- may come along.
    seen = {}

    def _run(argv, **kw):
        seen.update(argv=argv, **kw)
        return subprocess.CompletedProcess(argv, prootcheck._CONFINED)

    monkeypatch.setattr(prootcheck.subprocess, "run", _run)
    monkeypatch.setenv("BASH_ENV", "/tmp/evil-rc")

    prootcheck.confining_env("/usr/bin/proot", {
        "PATH": "/image/bin", "BASH_ENV": "/image/rc", "ENV": "/image/rc",
        "PROOT_NO_SECCOMP": "1", "PROOT_L2S_DIR": "/somewhere",
    })

    assert seen["env"] == {"PATH": os.defpath, "PROOT_NO_SECCOMP": "1"}
    assert seen["argv"][0] == "/usr/bin/proot"
    assert seen["argv"][1] == "--rootfs=/"
    assert seen["argv"][3] == prootcheck._PROBE_SHELL
    assert os.path.isabs(seen["argv"][3])


# --- what confining_env() makes of the verdicts -----------------------------

@pytest.fixture
def scripted(monkeypatch):
    """Patch _probe to answer from a list; record the toggles it was given."""
    calls = []
    answers = []

    def _probe(proot_bin, toggles):
        calls.append(dict(toggles))
        return answers.pop(0)

    monkeypatch.setattr(prootcheck, "_probe", _probe)
    return answers, calls


def test_a_confining_proot_needs_nothing(scripted, capsys):
    answers, calls = scripted
    answers[:] = [True]

    assert prootcheck.confining_env("/p", {}) == {}
    assert calls == [{}]
    assert capsys.readouterr().err == ""


def test_a_proot_leaking_in_seccomp_mode_gets_no_seccomp(scripted, capsys):
    answers, calls = scripted
    answers[:] = [False, True]

    assert prootcheck.confining_env("/p", {}) == {"PROOT_NO_SECCOMP": "1"}
    assert calls == [{}, {"PROOT_NO_SECCOMP": "1"}]
    err = capsys.readouterr().err
    assert "skips path translation" in err
    assert "PROOT_NO_SECCOMP=1" in err


def test_the_verdict_is_kept_for_the_process(scripted, capsys):
    answers, calls = scripted
    answers[:] = [False, True]

    first = prootcheck.confining_env("/p", {})
    first["PROOT_NO_SECCOMP"] = "tampered"
    again = prootcheck.confining_env("/p", {})

    assert again == {"PROOT_NO_SECCOMP": "1"}
    assert len(calls) == 2
    # Warned once, not once per RUN step.
    assert capsys.readouterr().err.count("skips path translation") == 1


def test_a_verdict_is_per_binary_and_toggle(scripted):
    answers, calls = scripted
    answers[:] = [True, True, True]

    prootcheck.confining_env("/p", {})
    prootcheck.confining_env("/q", {})
    prootcheck.confining_env("/p", {"PROOT_NO_SECCOMP": "1"})

    assert len(calls) == 3


def test_a_leak_with_seccomp_already_off_is_refused(scripted):
    answers, calls = scripted
    answers[:] = [False]

    with pytest.raises(prootcheck.ProotConfinementError) as exc:
        prootcheck.confining_env("/p", {"PROOT_NO_SECCOMP": "1"})
    assert "does not confine" in str(exc.value)
    assert calls == [{"PROOT_NO_SECCOMP": "1"}]


@pytest.mark.parametrize("retry", [False, "self-test exited with status 1"])
def test_a_leak_the_fallback_does_not_fix_is_refused(scripted, retry):
    answers, _calls = scripted
    answers[:] = [False, retry]

    with pytest.raises(prootcheck.ProotConfinementError):
        prootcheck.confining_env("/p", {})


def test_a_refusal_is_not_cached_as_a_pass(scripted):
    answers, _calls = scripted
    answers[:] = [False, False, False, False]

    for _ in range(2):
        with pytest.raises(prootcheck.ProotConfinementError):
            prootcheck.confining_env("/p", {})


def test_an_unanswered_probe_falls_back_to_no_seccomp(scripted, capsys):
    answers, calls = scripted
    answers[:] = ["self-test gave no answer within 30 seconds"]

    assert prootcheck.confining_env("/p", {}) == {"PROOT_NO_SECCOMP": "1"}
    assert calls == [{}]
    err = capsys.readouterr().err
    assert "could not verify" in err
    assert "no answer within 30 seconds" in err


def test_an_unanswered_probe_with_seccomp_off_adds_nothing(scripted, capsys):
    answers, _calls = scripted
    answers[:] = ["self-test exited with status 1"]

    assert prootcheck.confining_env(
        "/p", {"PROOT_NO_SECCOMP": "1"}) == {}
    assert "could not verify" in capsys.readouterr().err


def test_warnings_respect_quiet(scripted, capsys):
    answers, _calls = scripted
    answers[:] = [False, True, "self-test exited with status 1"]
    message.set_quiet(True)

    prootcheck.confining_env("/p", {})
    prootcheck.confining_env("/q", {})

    assert capsys.readouterr().err == ""


def test_an_empty_toggle_counts_as_unset(scripted):
    # proot itself treats any value as set, but login and RUN only forward
    # a non-empty one, so an empty one never reaches proot.
    answers, calls = scripted
    answers[:] = [True]

    prootcheck.confining_env("/p", {"PROOT_NO_SECCOMP": ""})

    assert calls == [{}]


def test_the_refusal_names_the_binary_safely(scripted):
    answers, _calls = scripted
    answers[:] = [False, False]

    with pytest.raises(prootcheck.ProotConfinementError) as exc:
        prootcheck.confining_env("/opt/\x1b[2Jproot", {})
    assert "\x1b" not in str(exc.value)
