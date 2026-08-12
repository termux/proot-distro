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


# ----- quote_path ---------------------------------------------------------

def test_quote_path_leaves_ordinary_names_alone():
    for name in ("/etc/passwd", "a b c.txt", "-dash", "юникод", "e'quote"):
        assert message.quote_path(name) == name


def test_quote_path_escapes_terminal_control_sequences():
    """A rootfs name is the guest's to choose, and both commands print it."""
    assert message.quote_path("a\x1b[31mRED\x1b[0m") == "a\\e[31mRED\\e[0m"
    assert message.quote_path("hide\rme") == "hide\\rme"
    assert message.quote_path("two\nlines") == "two\\nlines"
    assert message.quote_path("tab\there") == "tab\\there"


def test_quote_path_escapes_every_control_byte_and_del():
    quoted = message.quote_path("".join(chr(c) for c in range(0x20)) + "\x7f")
    assert "\x1b" not in quoted
    assert not any(ch < " " for ch in quoted)
    assert "\\x00" in quoted and "\\x7f" in quoted


def test_quote_path_escapes_backslash_so_escapes_are_unambiguous():
    """`a\\e[0m` typed into a name must not read as an ESC we escaped."""
    assert message.quote_path("a\\e[0m") == "a\\\\e[0m"
    assert message.quote_path("a\x1b[0m") == "a\\e[0m"
