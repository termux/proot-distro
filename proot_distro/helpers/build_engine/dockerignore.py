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

# Architecture: .dockerignore pattern matcher. fnmatch-based; supports
# leading `**` segments (collapsed to a single `*`) and prefix matching,
# so a bare pattern like `node_modules` correctly ignores everything
# below it. Negation (`!pattern`) works by toggling the verdict in
# pattern order — same precedence rule Docker itself uses.

import fnmatch
import glob as _glob
import os


def load_dockerignore(build_dir):
    """Return the list of `.dockerignore` patterns from *build_dir*."""
    path = os.path.join(build_dir, ".dockerignore")
    patterns = []
    try:
        with open(path) as fh:
            for line in fh:
                s = line.rstrip("\n").rstrip("\r").strip()
                if not s or s.startswith("#"):
                    continue
                patterns.append(s)
    except OSError:
        pass
    return patterns


def is_ignored(rel_path, patterns):
    """Return True iff *rel_path* matches the loaded ignore patterns."""
    if not patterns:
        return False
    # `Dockerfile` and `.dockerignore` themselves are never ignored.
    if rel_path in ("Dockerfile", ".dockerignore"):
        return False
    ignored = False
    for pat in patterns:
        negate = pat.startswith("!")
        p = pat[1:] if negate else pat
        if _match(rel_path, p):
            ignored = not negate
    return ignored


def _match(rel_path, pattern):
    pat = pattern.replace(os.sep, "/").strip("/")
    rel = rel_path.replace(os.sep, "/").strip("/")
    if "**" in pat:
        pat = pat.replace("**", "*")
    if fnmatch.fnmatchcase(rel, pat):
        return True
    # Prefix match: a pattern like `node_modules` ignores its children.
    parts = rel.split("/")
    for i in range(1, len(parts) + 1):
        prefix = "/".join(parts[:i])
        if fnmatch.fnmatchcase(prefix, pat):
            return True
    return False


def simple_glob(base, pattern):
    """Tiny glob: supports * and ? only (no ** recursion). Returns rel paths."""
    abs_pat = os.path.join(base, pattern)
    matches = _glob.glob(abs_pat)
    return [os.path.relpath(p, base) for p in matches]
