# Tests for proot_distro.helpers.dockerfile — the Dockerfile parser and the
# variable-expansion engine.

import pytest

from proot_distro.helpers import dockerfile as df


def _names(instrs):
    return [i["name"] for i in instrs]


def test_basic_parse():
    _d, instrs = df.parse_dockerfile("FROM ubuntu:24.04\nRUN echo hi\n")
    assert _names(instrs) == ["FROM", "RUN"]
    assert instrs[0]["value"] == "ubuntu:24.04"
    assert instrs[1]["value"] == "echo hi"


def test_line_continuation():
    _d, instrs = df.parse_dockerfile("RUN echo a \\\n  b c\n")
    assert instrs[0]["value"] == "echo a b c"


def test_comment_between_continuations_skipped():
    text = "RUN echo a \\\n# a comment\n  b\n"
    _d, instrs = df.parse_dockerfile(text)
    assert instrs[0]["value"] == "echo a b"


def test_full_line_comment_ignored():
    _d, instrs = df.parse_dockerfile("# hello\nFROM x\n")
    assert _names(instrs) == ["FROM"]


def test_escape_directive_backtick():
    text = "# escape=`\nFROM x\nRUN echo a `\n  b\n"
    directives, instrs = df.parse_dockerfile(text)
    assert directives["escape"] == "`"
    assert instrs[1]["value"] == "echo a b"


def test_syntax_directive_collected():
    directives, _ = df.parse_dockerfile(
        "# syntax=docker/dockerfile:1\nFROM x\n"
    )
    assert directives["syntax"] == "docker/dockerfile:1"


def test_exec_form_detected():
    _d, instrs = df.parse_dockerfile('CMD ["a", "b c"]\n')
    assert instrs[0]["exec_form"] is True
    assert instrs[0]["value"] == ["a", "b c"]


def test_shell_form_not_exec():
    _d, instrs = df.parse_dockerfile("CMD echo hi\n")
    assert instrs[0]["exec_form"] is False
    assert instrs[0]["value"] == "echo hi"


def test_heredoc_run():
    text = "RUN <<EOF\nline1\nline2\nEOF\n"
    _d, instrs = df.parse_dockerfile(text)
    assert instrs[0]["name"] == "RUN"
    assert instrs[0]["heredocs"][0]["body"] == "line1\nline2\n"


def test_heredoc_strip_indent():
    text = "RUN <<-EOF\n\tindented\n\tEOF\n"
    _d, instrs = df.parse_dockerfile(text)
    hd = instrs[0]["heredocs"][0]
    assert hd["strip_indent"] is True
    assert hd["body"] == "indented\n"


def test_unterminated_heredoc_raises():
    with pytest.raises(df.DockerfileSyntaxError):
        df.parse_dockerfile("RUN <<EOF\nno terminator\n")


def test_flag_parsing():
    _d, instrs = df.parse_dockerfile("COPY --chown=1000:1000 src dst\n")
    assert instrs[0]["flags"]["chown"] == "1000:1000"
    assert instrs[0]["value"] == "src dst"


def test_quoted_flag_value():
    _d, instrs = df.parse_dockerfile('COPY --chown="user:grp" a b\n')
    assert instrs[0]["flags"]["chown"] == "user:grp"


def test_onbuild_wraps_inner():
    _d, instrs = df.parse_dockerfile("ONBUILD RUN echo hi\n")
    assert instrs[0]["name"] == "ONBUILD"
    inner = instrs[0]["value"]
    assert inner["name"] == "RUN"
    assert inner["value"] == "echo hi"


def test_onbuild_nested_rejected():
    with pytest.raises(df.DockerfileSyntaxError):
        df.parse_dockerfile("ONBUILD ONBUILD RUN x\n")


def test_unknown_instruction_rejected():
    with pytest.raises(df.DockerfileSyntaxError):
        df.parse_dockerfile("FOOBAR baz\n")


def test_bom_and_crlf_normalised():
    _d, instrs = df.parse_dockerfile("﻿FROM x\r\nRUN y\r\n")
    assert _names(instrs) == ["FROM", "RUN"]


# ----- expand_vars --------------------------------------------------------

@pytest.mark.parametrize("text,env,expected", [
    ("$FOO", {"FOO": "bar"}, "bar"),
    ("${FOO}", {"FOO": "bar"}, "bar"),
    ("a${FOO}b", {"FOO": "X"}, "aXb"),
    ("$UNSET", {}, ""),
    ("${X:-def}", {}, "def"),
    ("${X:-def}", {"X": ""}, "def"),
    ("${X:-def}", {"X": "v"}, "v"),
    ("${X-def}", {}, "def"),
    ("${X-def}", {"X": ""}, ""),
    ("${X:+y}", {"X": "v"}, "y"),
    ("${X:+y}", {"X": ""}, ""),
    ("${X+y}", {"X": ""}, "y"),
    ("${X+y}", {}, ""),
    (r"\$FOO", {"FOO": "bar"}, "$FOO"),
    ("price$", {}, "price$"),
])
def test_expand_vars(text, env, expected):
    assert df.expand_vars(text, env) == expected


def test_expand_vars_unterminated_brace():
    with pytest.raises(df.DockerfileSyntaxError):
        df.expand_vars("${FOO", {})


def test_expand_vars_unset_vs_empty_distinction():
    # None means "unset"; "" means "set but empty".
    assert df.expand_vars("${X-d}", {"X": None}) == "d"
    assert df.expand_vars("${X-d}", {"X": ""}) == ""
