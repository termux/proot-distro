#
# Proot-Distro - manage proot containers.
#
# Created by Sylirre <sylirre@termux.dev> for Termux project.
# Development assisted by Claude Code (https://claude.ai/code).
#
# This program is free software: you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation, either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.
#

# Architecture: Pure Dockerfile parser. Returns a list of instruction
# records that the build engine consumes. No execution, no filesystem
# access. Handles parser directives (only `escape=` changes lexer
# behaviour), line continuations, full-line comments, JSON exec form
# detection, here-docs for RUN/COPY/ADD, and per-instruction flag
# parsing. Variable expansion is intentionally not performed here —
# the engine does that with the live ARG/ENV scope.

import json
import re
import shlex


# All Dockerfile instructions, per
# https://docs.docker.com/reference/dockerfile/. MAINTAINER is
# deprecated but still common in the wild; we accept it.
_INSTRUCTIONS = frozenset({
    "ADD", "ARG", "CMD", "COPY", "ENTRYPOINT", "ENV", "EXPOSE",
    "FROM", "HEALTHCHECK", "LABEL", "MAINTAINER", "ONBUILD", "RUN",
    "SHELL", "STOPSIGNAL", "USER", "VOLUME", "WORKDIR",
})

# Instructions that may carry a here-doc body (<<TAG ... TAG).
_HEREDOC_INSTRUCTIONS = frozenset({"ADD", "COPY", "RUN"})

# Parser-directive names recognised at the top of a Dockerfile. All
# other `# k=v` lines after the first non-directive line become
# normal comments.
_DIRECTIVES = frozenset({"syntax", "escape", "check"})


class DockerfileSyntaxError(Exception):
    """Raised when the Dockerfile cannot be parsed."""


# ---------------------------------------------------------------------------
# Top-level entry
# ---------------------------------------------------------------------------

def parse_dockerfile(text: str):
    """Parse Dockerfile content into (directives, instructions).

    directives is a dict of recognised parser directives (keys: 'syntax',
    'escape', 'check'). instructions is an ordered list of records:

        {
            "name":       str,         # upper-case instruction name
            "flags":      dict,        # --key=value flags before the value
            "value":      str | list,  # raw value, or list when exec form
            "exec_form":  bool,        # True iff value parsed as JSON array
            "heredocs":   list,        # list of {"tag", "strip_indent", "body"}
            "lineno":     int,         # 1-based source line of the first token
            "raw":        str,         # joined (continuation-merged) source
        }
    """
    if not isinstance(text, str):
        text = text.decode("utf-8", errors="replace")

    text = text.replace("\r\n", "\n").replace("\r", "\n")
    if text.startswith("﻿"):
        text = text[1:]

    raw_lines = text.split("\n")

    directives, directive_end = _parse_directives(raw_lines)
    escape_char = directives.get("escape", "\\") or "\\"
    if escape_char not in ("\\", "`"):
        # Spec only allows backslash or backtick; treat anything else as
        # an ordinary comment and fall back to backslash.
        escape_char = "\\"
        directives.pop("escape", None)

    instructions = _parse_instructions(raw_lines, directive_end, escape_char)
    return directives, instructions


# ---------------------------------------------------------------------------
# Parser directives
# ---------------------------------------------------------------------------

_DIRECTIVE_RE = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.+?)\s*$")


def _parse_directives(raw_lines):
    """Collect leading `# key=value` parser directives.

    Stops at the first non-blank, non-directive-comment line. Returns
    (directives_dict, index_of_first_post_directive_line).
    """
    directives = {}
    idx = 0
    n = len(raw_lines)
    while idx < n:
        line = raw_lines[idx]
        stripped = line.strip()
        if not stripped:
            idx += 1
            continue
        if not stripped.startswith("#"):
            break
        inner = stripped[1:]
        m = _DIRECTIVE_RE.match(inner)
        if not m:
            break
        key = m.group(1).lower()
        if key not in _DIRECTIVES:
            break
        if key in directives:
            # Spec: a duplicate directive ends the directive zone and the
            # duplicate is treated as a comment.
            break
        directives[key] = m.group(2).strip()
        idx += 1
    return directives, idx


# ---------------------------------------------------------------------------
# Instruction tokenisation
# ---------------------------------------------------------------------------

# A flag at the start of a value, e.g. --from=builder or --chmod=755.
# Flag values are non-space tokens; quoted values are handled by an
# explicit pre-pass before this matcher runs.
_FLAG_RE = re.compile(r"^\s*--([A-Za-z][A-Za-z0-9_-]*)(?:=(\S*))?(?=\s|$)")

# A here-doc opener: <<TAG, <<-TAG, <<"TAG", <<'TAG', <<-'TAG', ...
# The optional dash means "strip leading tabs from body and closing tag"
# in the spec; we honour that during body collection.
_HEREDOC_RE = re.compile(r"""<<(-?)(["']?)([A-Za-z_][A-Za-z0-9_]*)\2""")


def _parse_instructions(raw_lines, start_idx, escape_char):
    instructions = []
    n = len(raw_lines)
    i = start_idx

    while i < n:
        raw_line = raw_lines[i]
        line_no = i + 1
        stripped = raw_line.lstrip()

        if not stripped:
            i += 1
            continue

        if stripped.startswith("#"):
            # Full-line comment.
            i += 1
            continue

        # Accumulate continuations. Comments and blank lines between
        # continued segments are skipped, matching Docker's behaviour.
        accumulated_parts = [raw_line]
        cur = raw_line
        while _ends_with_escape(cur, escape_char):
            # Strip the trailing escape character (and any trailing
            # whitespace before it) from this segment.
            accumulated_parts[-1] = _strip_trailing_escape(
                accumulated_parts[-1], escape_char
            )
            i += 1
            # Skip blank lines and comment lines.
            while i < n:
                nxt = raw_lines[i]
                nxt_lstripped = nxt.lstrip()
                if not nxt_lstripped:
                    i += 1
                    continue
                if nxt_lstripped.startswith("#"):
                    i += 1
                    continue
                break
            if i >= n:
                cur = ""
                break
            cur = raw_lines[i]
            accumulated_parts.append(cur)

        # Logical line: join all parts with a single space.
        accumulated = " ".join(p.strip() for p in accumulated_parts).strip()
        if not accumulated:
            i += 1
            continue

        # Split the first whitespace-delimited token as the instruction.
        m = re.match(r"^\s*(\S+)\s*(.*)$", accumulated)
        if not m:
            i += 1
            continue
        name = m.group(1).upper()
        rest = m.group(2)

        if name not in _INSTRUCTIONS:
            raise DockerfileSyntaxError(
                f"Unknown instruction '{name}' at line {line_no}."
            )

        # ONBUILD wraps another instruction. Parse the inner part
        # recursively below.
        is_onbuild = (name == "ONBUILD")
        if is_onbuild:
            # Strip the inner instruction's name and parse the rest as
            # a normal instruction record.
            inner_match = re.match(r"^\s*(\S+)\s*(.*)$", rest)
            if not inner_match:
                raise DockerfileSyntaxError(
                    f"ONBUILD without inner instruction at line {line_no}."
                )
            inner_name = inner_match.group(1).upper()
            if inner_name not in _INSTRUCTIONS or inner_name == "ONBUILD":
                raise DockerfileSyntaxError(
                    f"Invalid ONBUILD inner instruction '{inner_name}' at "
                    f"line {line_no}."
                )
            outer_name = name
            name = inner_name
            rest = inner_match.group(2)

        flags, rest = _parse_flags(rest)
        value = rest.strip()

        heredocs = []
        if name in _HEREDOC_INSTRUCTIONS:
            here_tags = _extract_heredoc_tags(value)
            for strip_indent, tag in here_tags:
                body, i = _collect_heredoc_body(
                    raw_lines, i + 1, tag, strip_indent
                )
                heredocs.append({
                    "tag": tag,
                    "strip_indent": strip_indent,
                    "body": body,
                })
            if not here_tags:
                i += 1
        else:
            i += 1

        exec_form, parsed_value = _try_exec_form(value)

        record = {
            "name": name,
            "flags": flags,
            "value": parsed_value if exec_form else value,
            "exec_form": exec_form,
            "heredocs": heredocs,
            "lineno": line_no,
            "raw": accumulated,
        }

        if is_onbuild:
            instructions.append({
                "name": "ONBUILD",
                "flags": {},
                "value": record,        # the wrapped inner instruction
                "exec_form": False,
                "heredocs": [],
                "lineno": line_no,
                "raw": accumulated,
            })
        else:
            instructions.append(record)

    return instructions


def _ends_with_escape(line, escape_char):
    """True if `line` ends with the escape character (continuation)."""
    s = line.rstrip()
    if not s:
        return False
    if not s.endswith(escape_char):
        return False
    # Detect an escaped escape character ("\\\\" with escape='\\'):
    # an odd number of trailing escapes means continuation; an even
    # number means a literal trailing escape.
    cnt = 0
    j = len(s) - 1
    while j >= 0 and s[j] == escape_char:
        cnt += 1
        j -= 1
    return (cnt % 2) == 1


def _strip_trailing_escape(line, escape_char):
    """Remove the continuation escape (and any trailing whitespace)."""
    s = line.rstrip()
    if s.endswith(escape_char):
        s = s[:-1]
    return s.rstrip()


def _parse_flags(text):
    """Pull leading --key[=value] flags off `text`.

    Returns (flags_dict, remaining_text). Flag values that contain
    spaces must be quoted with shlex syntax (`--chown="user:group"`),
    and we use shlex's POSIX mode to strip the quotes; un-quoted flag
    values stop at the next whitespace token.
    """
    flags = {}
    while True:
        m = _FLAG_RE.match(text)
        if not m:
            break
        key = m.group(1)
        val = m.group(2) if m.group(2) is not None else ""
        # If the matched value contains quote chars that shlex would
        # strip, re-parse a single token via shlex to recover the
        # unquoted form.
        if "=" in m.group(0):
            after_eq = m.group(0).split("=", 1)[1]
            if after_eq and after_eq[0] in ('"', "'"):
                # Try shlex over a slice starting at the value char.
                try:
                    rest_after = text[m.start() + m.group(0).index("=") + 1:]
                    lex = shlex.shlex(rest_after, posix=True)
                    lex.whitespace_split = True
                    lex.commenters = ""
                    val = next(iter(lex))
                    # Skip past the consumed token in the source.
                    consumed = (
                        m.start() + m.group(0).index("=") + 1
                        + _shlex_consumed_len(rest_after, val)
                    )
                    flags[key] = val
                    text = text[consumed:]
                    continue
                except (StopIteration, ValueError):
                    pass
        flags[key] = val
        text = text[m.end():]
    return flags, text


def _shlex_consumed_len(source, parsed_token):
    """Best-effort: count source bytes that shlex consumed for one token."""
    # Find the original closing quote and step past trailing whitespace.
    if not source:
        return 0
    quote = source[0]
    if quote in ('"', "'"):
        i = 1
        while i < len(source) and source[i] != quote:
            # Honour POSIX backslash-escape inside double quotes.
            if quote == '"' and source[i] == "\\" and i + 1 < len(source):
                i += 2
                continue
            i += 1
        return i + 1
    # Unquoted: token ran until next whitespace.
    return len(parsed_token)


def _extract_heredoc_tags(value):
    """Return [(strip_indent_bool, tag_name), ...] for here-doc openers."""
    tags = []
    for m in _HEREDOC_RE.finditer(value):
        tags.append((bool(m.group(1)), m.group(3)))
    return tags


def _collect_heredoc_body(raw_lines, start_i, tag, strip_indent):
    """Read raw lines until the line that exactly matches `tag`.

    With strip_indent (i.e. <<-TAG), leading tabs are stripped from
    every body line and from the closing-tag line before comparison.

    Returns (body_text_with_trailing_newline, idx_one_past_closing_tag).
    """
    body = []
    i = start_i
    n = len(raw_lines)
    while i < n:
        line = raw_lines[i]
        cmp_line = line.lstrip("\t") if strip_indent else line
        if cmp_line == tag or cmp_line.rstrip() == tag:
            return "\n".join(body) + ("\n" if body else ""), i + 1
        body.append(line.lstrip("\t") if strip_indent else line)
        i += 1
    raise DockerfileSyntaxError(
        f"Unterminated here-doc body for tag '{tag}'."
    )


def _try_exec_form(value):
    """Detect JSON-array exec form. Returns (is_exec_form, parsed_list_or_None)."""
    s = value.strip()
    if not (s.startswith("[") and s.endswith("]")):
        return False, None
    try:
        parsed = json.loads(s)
    except (json.JSONDecodeError, ValueError):
        return False, None
    if not isinstance(parsed, list):
        return False, None
    if not all(isinstance(x, str) for x in parsed):
        return False, None
    return True, parsed


# ---------------------------------------------------------------------------
# Variable expansion (used by build engine, not during parse)
# ---------------------------------------------------------------------------

def expand_vars(text, env):
    r"""Expand $VAR, ${VAR}, ${VAR:-default}, ${VAR-default},
    ${VAR:+value}, ${VAR+value} against the given env mapping.

    Unknown variables expand to the empty string. Unset-vs-empty
    distinction is preserved: a None entry in `env` means "unset",
    while an empty string means "set but empty" (relevant for the
    `:-` / `:+` operators).

    A leading backslash escapes the following character (so `\$FOO`
    is treated as a literal dollar sign followed by FOO).
    """
    out = []
    i = 0
    n = len(text)
    while i < n:
        c = text[i]
        if c == "\\" and i + 1 < n:
            out.append(text[i + 1])
            i += 2
            continue
        if c != "$":
            out.append(c)
            i += 1
            continue
        if i + 1 < n and text[i + 1] == "{":
            close = text.find("}", i + 2)
            if close < 0:
                raise DockerfileSyntaxError(
                    "Unterminated ${...} expression in value."
                )
            inner = text[i + 2:close]
            i = close + 1
            out.append(_expand_braced(inner, env))
        else:
            j = i + 1
            while j < n and (text[j].isalnum() or text[j] == "_"):
                j += 1
            if j == i + 1:
                out.append("$")
                i += 1
            else:
                name = text[i + 1:j]
                out.append(_lookup_or_empty(name, env))
                i = j
    return "".join(out)


_BRACED_OP_RE = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)(:[-+?]|[-+?])(.*)$")


def _expand_braced(inner, env):
    m = _BRACED_OP_RE.match(inner)
    if not m:
        return _lookup_or_empty(inner, env)
    name, op, arg = m.group(1), m.group(2), m.group(3)
    raw = env.get(name, None)
    if op == ":-":
        return arg if not raw else raw
    if op == "-":
        return arg if raw is None else raw
    if op == ":+":
        return arg if raw else ""
    if op == "+":
        return arg if raw is not None else ""
    # ":?" and "?" are bash-isms; we leave them as plain lookups.
    return _lookup_or_empty(name, env)


def _lookup_or_empty(name, env):
    val = env.get(name)
    return "" if val is None else str(val)
