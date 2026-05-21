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

# Architecture: All argparse plumbing for the CLI. Each subcommand is
# added by a focused builder; the top-level build_parser() composes
# them. Help is handled manually (add_help=False everywhere) so a
# missing required positional never produces an error instead of
# rendering the per-command help.
#
# A small _PdArgumentParser subclass exists so per-command parsers can
# stamp their canonical command name onto each instance — the value
# is consumed by the runtime error handler in cli.py to render the
# right help page when argparse rejects an argument.

import argparse
import sys

from proot_distro.constants import IS_TERMUX, PROGRAM_NAME
from proot_distro.message import msg, crit_error


class _PdArgumentParser(argparse.ArgumentParser):
    """Argparse subclass that defers errors to the CLI dispatcher."""

    _pd_command: "str | None" = None

    def error(self, message: str) -> None:
        # Late import to avoid an import cycle: parser.py is imported by
        # cli.py, and the per-command help renderer indirectly imports
        # cli.py via HELP_COMMANDS's references.
        from proot_distro.commands.help import HELP_COMMANDS

        msg()
        crit_error(message)
        if self._pd_command and self._pd_command in HELP_COMMANDS:
            HELP_COMMANDS[self._pd_command]()
        msg()
        sys.exit(1)


# Maps each canonical command to required (arg_name, error_message) pairs.
REQUIRED_ARGS = {
    "install": [("image_ref", "Docker image reference is not specified"
                 " (e.g. 'ubuntu:24.04').")],
    "remove":  [("container_name", "container name is not specified.")],
    "rename":  [("orig_name", "the original container name is not specified."),
                ("new_name",  "the new container name is not specified.")],
    "reset":   [("container_name", "container name is not specified.")],
    "login":   [("container_name", "container name is not specified.")],
    "backup":  [("container_name", "container name is not specified.")],
    "copy":    [("source",      "source path is not specified."),
                ("destination", "destination path is not specified.")],
    "sync":    [("source",      "source path is not specified."),
                ("destination", "destination path is not specified.")],
    "run":     [("container_name", "container name is not specified.")],
    "push":    [("image_ref", "image reference is not specified"
                 " (e.g. 'myrepo/myapp:1.0').")],
}


# Aliases for the canonical command names. Resolved by the CLI before
# dispatch so handlers always see the canonical form.
ALIAS_TO_CANONICAL = {
    "add": "install", "i": "install", "in": "install", "ins": "install",
    "rm": "remove",
    "sh": "login",
    "li": "list", "ls": "list",
    "bak": "backup", "bkp": "backup",
    "clear": "clear-cache", "cl": "clear-cache",
    "cp": "copy",
    "h": "help", "he": "help", "hel": "help",
}


def _add_login_or_run_common(p):
    """Options shared by both `login` and `run`."""
    p.add_argument("-u", "--user", default="root")
    _ports = p.add_mutually_exclusive_group()
    _ports.add_argument(
        "-P", "--redirect-ports", dest="redirect_ports", action="store_true"
    )
    if p.prog.endswith("login"):
        _ports.add_argument(
            "--fix-low-ports", dest="redirect_ports", action="store_true"
        )
    if IS_TERMUX:
        _iso = p.add_mutually_exclusive_group()
        _iso.add_argument("--isolated", action="store_true")
        _iso.add_argument("--minimal", action="store_true")
    _sh = p.add_mutually_exclusive_group()
    _sh.add_argument(
        "--shared-home", dest="shared_home", action="store_true"
    )
    _sh.add_argument(
        "--termux-home", dest="shared_home", action="store_true"
    )
    p.add_argument("--shared-tmp", dest="shared_tmp", action="store_true")
    p.add_argument("--shared-x11", dest="shared_x11", action="store_true")
    p.add_argument(
        "-b", "--bind", action="append", metavar="PATH[:PATH]"
    )
    if IS_TERMUX:
        p.add_argument(
            "--no-link2symlink", dest="no_link2symlink", action="store_true"
        )
        p.add_argument(
            "--no-sysvipc", dest="no_sysvipc", action="store_true"
        )
        p.add_argument(
            "--no-kill-on-exit", dest="no_kill_on_exit", action="store_true"
        )
    p.add_argument("--emulator", dest="emulator", metavar="PATH")
    p.add_argument("--kernel", metavar="STRING")
    p.add_argument("--hostname", metavar="STRING")
    p.add_argument("-w", "--work-dir", dest="work_dir", metavar="PATH")
    p.add_argument("-e", "--env", action="append", metavar="VAR=VALUE")


def build_parser() -> _PdArgumentParser:
    """Construct the top-level argparse parser with every subcommand."""
    parser = _PdArgumentParser(
        prog=PROGRAM_NAME,
        description="Manage Linux proot containers.",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("help", aliases=["hel", "he", "h"], add_help=False)

    _install(sub)
    _remove(sub)
    _rename(sub)
    _reset(sub)
    _login(sub)
    _list(sub)
    _backup(sub)
    _restore(sub)
    _clear_cache(sub)
    _copy(sub)
    _sync(sub)
    _build(sub)
    _push(sub)
    _run(sub)

    return parser


def _install(sub):
    p = sub.add_parser(
        "install", aliases=["add", "i", "in", "ins"], add_help=False
    )
    p._pd_command = "install"
    p.add_argument("image_ref", nargs="?", default=None, metavar="IMAGE")
    name_grp = p.add_mutually_exclusive_group()
    name_grp.add_argument(
        "-n", "--name", dest="custom_container_name", metavar="ALIAS"
    )
    name_grp.add_argument(
        "--override-alias", dest="custom_container_name", metavar="ALIAS"
    )
    p.add_argument(
        "-a", "--architecture", dest="override_arch", metavar="ARCH",
    )
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _remove(sub):
    p = sub.add_parser("remove", aliases=["rm"], add_help=False)
    p._pd_command = "remove"
    p.add_argument("container_name", nargs="?", default=None)
    vq = p.add_mutually_exclusive_group()
    vq.add_argument("-v", "--verbose", action="store_true")
    vq.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _rename(sub):
    p = sub.add_parser("rename", add_help=False)
    p._pd_command = "rename"
    p.add_argument("orig_name", nargs="?", default=None)
    p.add_argument("new_name", nargs="?", default=None)
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _reset(sub):
    p = sub.add_parser("reset", add_help=False)
    p._pd_command = "reset"
    p.add_argument("container_name", nargs="?", default=None)
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _login(sub):
    p = sub.add_parser("login", aliases=["sh"], add_help=False)
    p._pd_command = "login"
    p.add_argument("container_name", nargs="?", default=None)
    _add_login_or_run_common(p)
    p.add_argument("--get-proot-cmd", dest="get_proot_cmd", action="store_true")
    p.add_argument("login_cmd", nargs="*")
    p.add_argument("-h", "--help", action="store_true")


def _list(sub):
    p = sub.add_parser("list", aliases=["li", "ls"], add_help=False)
    p._pd_command = "list"
    p.add_argument("-h", "--help", action="store_true")
    p.add_argument("-q", "--quiet", action="store_true")


def _backup(sub):
    p = sub.add_parser("backup", aliases=["bak", "bkp"], add_help=False)
    p._pd_command = "backup"
    p.add_argument("container_name", nargs="?", default=None)
    p.add_argument("-o", "--output", metavar="FILE")
    p.add_argument(
        "-c", "--compress", dest="compression",
        choices=["gzip", "bzip2", "xz", "none"], metavar="TYPE",
    )
    vq = p.add_mutually_exclusive_group()
    vq.add_argument("-v", "--verbose", action="store_true")
    vq.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _restore(sub):
    p = sub.add_parser("restore", add_help=False)
    p._pd_command = "restore"
    p.add_argument("archive", nargs="?")
    vq = p.add_mutually_exclusive_group()
    vq.add_argument("-v", "--verbose", action="store_true")
    vq.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _clear_cache(sub):
    p = sub.add_parser(
        "clear-cache", aliases=["clear", "cl"], add_help=False
    )
    p._pd_command = "clear-cache"
    vq = p.add_mutually_exclusive_group()
    vq.add_argument("-v", "--verbose", action="store_true")
    vq.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _copy(sub):
    p = sub.add_parser("copy", aliases=["cp"], add_help=False)
    p._pd_command = "copy"
    p.add_argument("source", nargs="?", default=None)
    p.add_argument("destination", nargs="?", default=None)
    vq = p.add_mutually_exclusive_group()
    vq.add_argument("-v", "--verbose", action="store_true")
    vq.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-m", "--move", action="store_true")
    p.add_argument("-r", "--recursive", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _sync(sub):
    p = sub.add_parser("sync", add_help=False)
    p._pd_command = "sync"
    p.add_argument("source", nargs="?", default=None)
    p.add_argument("destination", nargs="?", default=None)
    vq = p.add_mutually_exclusive_group()
    vq.add_argument("-v", "--verbose", action="store_true")
    vq.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-c", "--checksum", action="store_true")
    p.add_argument("-d", "--delete", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _build(sub):
    p = sub.add_parser("build", add_help=False)
    p._pd_command = "build"
    p.add_argument("path", nargs="?", default=".", metavar="PATH")
    p.add_argument("-f", "--file", dest="dockerfile", metavar="PATH")
    p.add_argument(
        "-t", "--tag", dest="tags", action="append",
        default=[], metavar="REF",
    )
    p.add_argument(
        "--build-arg", dest="build_args", action="append",
        default=[], metavar="K=V",
    )
    p.add_argument(
        "-a", "--architecture", dest="override_arch", metavar="ARCH",
    )
    p.add_argument(
        "--target", dest="target_stage", metavar="STAGE",
    )
    p.add_argument("--emulator", dest="emulator", metavar="PATH")
    p.add_argument(
        "-o", "--output", dest="outputs", action="append",
        default=[], metavar="FILE",
    )
    p.add_argument(
        "--install-as", dest="install_as", metavar="NAME",
    )
    p.add_argument("--no-cache", dest="no_cache", action="store_true")
    vq = p.add_mutually_exclusive_group()
    vq.add_argument("-v", "--verbose", action="store_true")
    vq.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _push(sub):
    p = sub.add_parser("push", add_help=False)
    p._pd_command = "push"
    p.add_argument("image_ref", nargs="?", default=None, metavar="IMAGE")
    p.add_argument(
        "-a", "--architecture", dest="override_arch", metavar="ARCH",
    )
    p.add_argument("-q", "--quiet", action="store_true")
    p.add_argument("-h", "--help", action="store_true")


def _run(sub):
    p = sub.add_parser("run", add_help=False)
    p._pd_command = "run"
    p.add_argument("container_name", nargs="?", default=None)
    _add_login_or_run_common(p)
    p.add_argument("--get-proot-cmd", action="store_true")
    p.add_argument("-h", "--help", action="store_true")
