# Tests for proot_distro.message — quiet flag, TTY-safety probe, color
# gating, and the log helpers.

import pytest

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


def test_log_event_capture_is_structured_and_context_local(capsys):
    captured = []
    message.set_quiet(True)
    with message.capture_log_events(
        lambda kind, text, phase, resource: captured.append(
            (kind, text, phase, resource)
        )
    ):
        message.log_info("phase")
        message.log_retry("try again", resource="sha256:abc")
        message.warn("careful")
        message.log_error("failed")
        message.crit_error("fatal")

    assert captured == [
        ("info", "phase", None, None),
        ("retry", "try again", "network", "sha256:abc"),
        ("warning", "careful", None, None),
        ("error", "failed", None, None),
        ("error", "fatal", None, None),
    ]
    assert capsys.readouterr() == ("", "")

    message.set_quiet(False)
    message.log_info("outside")
    assert "outside" in capsys.readouterr().err


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


def test_quote_path_escapes_a_lone_surrogate():
    # How a byte that is not UTF-8 arrives, from argv, the environment or a
    # JSON `\\udcff`. stderr renders one as backslashreplace does, which is
    # where the spelling comes from; stdout is strict and used to raise.
    assert message.quote_path("a\udcffb") == "a\\udcffb"
    assert message.quote_path("\ud800") == "\\ud800"
    message.quote_path("x\udcff\ud800\x1b").encode("utf-8")  # strict: fine
    # And a backslash spelled out stays distinguishable from the escape.
    assert message.quote_path("\\udcff") == "\\\\udcff"


def test_quote_path_escapes_every_control_byte_and_del():
    quoted = message.quote_path("".join(chr(c) for c in range(0x20)) + "\x7f")
    assert "\x1b" not in quoted
    assert not any(ch < " " for ch in quoted)
    assert "\\x00" in quoted and "\\x7f" in quoted


def test_quote_path_escapes_backslash_so_escapes_are_unambiguous():
    """`a\\e[0m` typed into a name must not read as an ESC we escaped."""
    assert message.quote_path("a\\e[0m") == "a\\\\e[0m"
    assert message.quote_path("a\x1b[0m") == "a\\e[0m"


# ----- quote_error --------------------------------------------------------

def test_quote_error_uses_strerror_which_carries_no_filename():
    # OSError's own str() reprs the filename, which would come out
    # double-escaped once quote_path escaped the backslashes again. The
    # caller names the entry itself; this is only the reason.
    try:
        open("/nonexistent\x1b[31m/x")
    except OSError as exc:
        assert message.quote_error(exc) == "No such file or directory"


def test_quote_error_escapes_an_exception_that_interpolated_a_name():
    # A TarError, a BuildError or a RuntimeError raised while an image was
    # unpacked has no strerror and builds its message by interpolation, so
    # the whole string is untrusted.
    import tarfile

    exc = tarfile.TarError("bad member 'a\x1b[31mRED\x1b[0m'")
    assert message.quote_error(exc) == "bad member 'a\\e[31mRED\\e[0m'"
    assert "\x1b" not in message.quote_error(exc)


def test_quote_error_falls_back_when_strerror_is_none():
    exc = OSError()
    assert "\x1b" not in message.quote_error(exc)


# ----- C1 controls and format characters ------------------------------------

@pytest.mark.parametrize("raw, quoted", [
    ("a\x9b2Jb", "a\\x9b2Jb"),          # C1 CSI: a terminal's ESC [
    ("a\x85b", "a\\x85b"),              # C1 NEL: a line break to some
    ("a\x80\x9fb", "a\\x80\\x9fb"),     # the ends of the C1 range
    ("evil\u202etxt.exe", "evil\\u202etxt.exe"),   # right-to-left override
    ("a\u2066b\u2069", "a\\u2066b\\u2069"),          # a bidi isolate
    ("pass\u200bwd", "pass\\u200bwd"),               # zero-width space
    ("a\ufeffb", "a\\ufeffb"),                       # BOM / ZWNBSP
    ("soft\xadhyphen", "soft\\xadhyphen"),           # soft hyphen
    ("one\u2028two\u2029", "one\\u2028two\\u2029"),  # line/paragraph sep
    ("tag\U000e0001", "tag\\U000e0001"),             # an astral format char
])
def test_quote_path_escapes_c1_controls_and_format_characters(raw, quoted):
    # quote_path used to stop at C0 and DEL: an HTTP reason phrase is
    # decoded as Latin-1, so a server's byte 0x9b reached the terminal as
    # U+009B, and a bidi override made a name read as another.
    assert message.quote_path(raw) == quoted


@pytest.mark.parametrize("name", [
    "caf\xe9", "no\xa0break", "\u3000wide", "\ue000private", "\U0001f600",
    "\u4e2d\u6587", "\u0627\u0644\u0639\u0631\u0628\u064a\u0629",
])
def test_quote_path_still_leaves_other_text_alone(name):
    # Non-ASCII spaces, private use, emoji and right-to-left *letters* are
    # shown as they are: only controls and format characters are escaped.
    assert message.quote_path(name) == name


# ----- printable_message --------------------------------------------------

def test_printable_message_escapes_controls_and_format_characters():
    text = "HTTP Error 404: \x1b]0;pwned\x07\x9b2J\u202eok\rover"
    assert message.printable_message(text) == (
        "HTTP Error 404: \\e]0;pwned\\x07\\x9b2J\\u202eok\\rover")


def test_printable_message_keeps_the_layout_of_a_message():
    # A message may run over lines and line its parts up with tabs.
    text = "first line\n\tsecond line"
    assert message.printable_message(text) == text


def test_printable_message_leaves_what_quote_path_escaped_unchanged():
    # Backslashes stay as they are, so a message built with quote_path()
    # reads the same once the log helper has seen it too.
    quoted = "cannot copy '" + message.quote_path("a\x1b\\b\u202e") + "'"
    assert message.printable_message(quoted) == quoted
    assert message.printable_message("C:\\dir\\x41") == "C:\\dir\\x41"


def test_printable_message_shows_a_non_string_as_its_str():
    assert message.printable_message(ValueError("bad\x9b")) == "bad\\x9b"


@pytest.mark.parametrize("helper", [
    "log_info", "log_retry", "log_error", "warn", "crit_error",
])
def test_every_log_helper_escapes_what_it_prints(helper, capsys):
    # Messages interpolate others' text in many places without quoting it
    # ("Network error: {exc}" quotes a server's reason phrase), and the
    # log helpers printed whatever they were given.
    getattr(message, helper)("x\x1b]0;pwned\x07\x9b2J\u202ey")
    err = capsys.readouterr().err
    for char in ("\x1b", "\x07", "\x9b", "\u202e"):
        assert char not in err
    assert "x\\e]0;pwned\\x07\\x9b2J\\u202ey" in err


@pytest.mark.parametrize("helper, kind", [
    ("log_info", "info"), ("log_retry", "retry"), ("log_error", "error"),
    ("warn", "warning"), ("crit_error", "error"),
])
def test_every_log_helper_escapes_the_event_it_hands_on(helper, kind):
    # Under the API worker the helpers emit events instead of printing,
    # and an API caller prints those too.
    seen = []
    with message.capture_log_events(
            lambda k, text, phase, resource: seen.append((k, text))):
        getattr(message, helper)("x\x9b2J\u202ey")
    assert seen == [(kind, "x\\x9b2J\\u202ey")]


# ----- msg ----------------------------------------------------------------

_RLO = "\N{RIGHT-TO-LEFT OVERRIDE}"


def test_msg_escapes_what_it_prints(capsys):
    # Regression: msg() printed its arguments as they were, so a caller
    # that interpolated someone else's text without quote_path() put that
    # text's escape sequences on the terminal.
    message.msg("value:", f"x\x1b]0;pwned\x07\x9b2J{_RLO}y")
    assert capsys.readouterr().err == (
        "value: x\\e]0;pwned\\x07\\x9b2J\\u202ey\n")


def test_msg_keeps_its_own_colours_and_escapes_any_other(
        monkeypatch, capsys):
    colors = message._COLORS
    monkeypatch.setattr(message, "C", colors)
    message.msg(f"{colors['CYAN']}Command: {colors['GREEN']}"
                f"x\x1b[8mhidden\x1b]0;t\x07{colors['RST']}")
    assert capsys.readouterr().err == (
        f"{colors['CYAN']}Command: {colors['GREEN']}"
        f"x\\e[8mhidden\\e]0;t\\x07{colors['RST']}\n")
    # Before the first colour and after the last one too.
    message.msg(f"\x9bA{colors['RED']}x\x1b[2J")
    assert capsys.readouterr().err == (
        f"\\x9bA{colors['RED']}x\\e[2J\n")


def test_msg_lets_no_colour_through_while_colours_are_off(capsys):
    # Off a terminal C is empty, and so is what may pass unescaped.
    message.msg(message._COLORS["RED"] + "x")
    assert capsys.readouterr().err == "\\e[0m\\e[31mx\n"


def test_msg_prints_its_arguments_as_print_would(capsys):
    message.msg(1, None, "a")
    assert capsys.readouterr().err == "1 None a\n"
    message.msg()
    assert capsys.readouterr().err == "\n"
    message.msg("first\n\tsecond")
    assert capsys.readouterr().err == "first\n\tsecond\n"
