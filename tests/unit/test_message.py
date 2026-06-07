# Tests for proot_distro.message — quiet flag, TTY-safety probe, color
# gating, and the log helpers.

from proot_distro import message


def test_quiet_toggle():
    assert message.is_quiet() is False
    message.set_quiet(True)
    assert message.is_quiet() is True
    message.set_quiet(False)
    assert message.is_quiet() is False


def test_tty_safe_for_writes_non_tty_is_true():
    # stderr is not a real TTY under pytest capture -> writes are safe.
    assert message.tty_safe_for_writes() is True


def test_colors_disabled_in_test_env():
    # PD_FORCE_NO_COLORS=1 (set in conftest) + non-TTY => empty palette.
    assert message.C["RED"] == ""
    assert message.C["RST"] == ""
    assert message.C["BGREEN"] == ""


def test_log_info_suppressed_when_quiet(capsys):
    message.set_quiet(True)
    message.log_info("hidden-info-line")
    out = capsys.readouterr()
    assert "hidden-info-line" not in out.err


def test_log_info_shown_when_not_quiet(capsys):
    message.set_quiet(False)
    message.log_info("visible-info-line")
    assert "visible-info-line" in capsys.readouterr().err


def test_log_error_always_shown_even_quiet(capsys):
    message.set_quiet(True)
    message.log_error("error-line")
    assert "error-line" in capsys.readouterr().err


def test_crit_error_format(capsys):
    message.crit_error("something bad")
    err = capsys.readouterr().err
    assert "Error:" in err
    assert "something bad" in err


def test_warn_format(capsys):
    message.warn("careful")
    err = capsys.readouterr().err
    assert "Warning:" in err
    assert "careful" in err
