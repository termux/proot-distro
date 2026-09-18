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

# Architecture: Small stateless helpers used by multiple handlers in the
# build engine. None of them touch the BuildEngine instance; keeping
# them at module level makes them trivially testable in isolation.

import shlex

from proot_distro.helpers.build_engine.errors import BuildError


def split_arg(value):
    """Parse `ARG K[=V]` value text. Returns (key, default_or_None)."""
    if isinstance(value, list):
        value = " ".join(value)
    s = str(value).strip()
    if not s:
        return ("", None)
    if "=" in s:
        k, _, v = s.partition("=")
        return (k.strip(), v)
    return (s, None)


def split_operands(value, instr):
    """shlex-split a shell-form instruction's operands, or raise BuildError.

    COPY/ADD's source and destination list, EXPOSE's ports and VOLUME's
    mount points are all written with shell quoting, and shlex answers
    an unbalanced quote or a trailing backslash with a ValueError. The
    Dockerfile is the user's own file, but `build` catches only
    BuildError and OSError, so one mistyped line ended the build in a
    traceback instead of naming the line that caused it.
    """
    try:
        return shlex.split(str(value))
    except ValueError as exc:
        raise BuildError(
            f"Cannot parse {instr['name']} at line {instr['lineno']}: "
            f"{exc}."
        ) from exc


# The bits a mode can carry: permissions plus setuid/setgid/sticky. It is
# what chmod(2) keeps of its argument, and Docker's own bound for --chmod.
MAX_FILE_MODE = 0o7777


def parse_chmod(value, instr):
    """The mode a COPY/ADD `--chmod=` names, or raise BuildError.

    Docker's rule: an octal string between 0 and 07777. The value used
    to be matched against `[0-7]+` and anything else was quietly no
    override at all, so `--chmod=0x755` or `--chmod=u+x` built an image
    with the sources' own modes and said nothing; and a string of octal
    digits that is one has no upper bound, so `--chmod=77777777777777`
    reached os.fchmod() as an int C cannot hold -- an OverflowError,
    which neither of `build`'s nets catches -- while a smaller excess
    was silently masked by the kernel and recorded whole in the layer.
    """
    text = str(value)
    if text and all(ch in "01234567" for ch in text):
        mode = int(text, 8)
        if mode <= MAX_FILE_MODE:
            return mode
    raise BuildError(
        f"Invalid {instr['name']} --chmod value '{text}' at line "
        f"{instr['lineno']}: it should be an octal string between 0 and "
        f"07777."
    )


def parse_kv_list(value):
    """Parse ENV/LABEL key=value pairs (with shell-like quoting)."""
    s = str(value).strip()
    if "=" not in s:
        # Legacy ENV form: `ENV KEY value` (no equals). Single pair.
        toks = s.split(None, 1)
        if len(toks) == 2:
            return [(toks[0], toks[1])]
        return [(s, "")]
    try:
        lex = shlex.shlex(s, posix=True)
        lex.whitespace_split = True
        lex.commenters = ""
        tokens = list(lex)
    except ValueError as exc:
        raise BuildError(f"Cannot parse key=value list: {exc}") from exc
    pairs = []
    for t in tokens:
        if "=" not in t:
            continue
        k, _, v = t.partition("=")
        pairs.append((k, v))
    return pairs


def to_argv(instr, default_shell):
    """Convert a CMD/ENTRYPOINT instruction into an argv list.

    Exec form: the value is already a list.
    Shell form: wrap the value with the default shell.
    """
    if instr["exec_form"]:
        return list(instr["value"])
    raw = str(instr["value"])
    return list(default_shell) + [raw]


def looks_like_url(s):
    return s.startswith(("http://", "https://"))


# How much of a file the signature check below needs: the ustar magic
# sits at offset 257 and runs to 265.
TAR_HEADER_BYTES = 265


def is_tar_header(head):
    """True when *head* opens a tar / tar.gz / tar.bz2 / tar.xz stream.

    A signature-only check, and it takes the bytes rather than a name:
    the one caller (ADD's auto-extract) already holds a descriptor on
    the file, so it sniffs the very inode it is about to read instead
    of resolving the path a second time and hoping for the same file.
    """
    if len(head) < TAR_HEADER_BYTES:
        return False
    if head[257:263] == b"ustar\x00" or head[257:265] == b"ustar  \x00":
        return True
    if head[:3] == b"\x1f\x8b\x08":
        return True
    if head[:3] == b"BZh":
        return True
    if head[:6] == b"\xfd7zXZ\x00":
        return True
    return False
