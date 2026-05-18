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

# Architecture: Entry point for all CLI parsing. Uses argparse with
# add_help=False so --help is handled manually, avoiding "required argument"
# errors when a subcommand's --help is invoked without positional args.
# Command aliases are resolved before dispatch so handlers always see the
# canonical command name. Missing required positionals are caught manually
# and trigger per-command help text.

import argparse
import os
import shutil
import signal
import subprocess
import sys

from proot_distro.constants import IS_TERMUX, PROGRAM_NAME
from proot_distro.colors import C, msg
from proot_distro.commands.install import command_install
from proot_distro.commands.remove import command_remove
from proot_distro.commands.rename import command_rename
from proot_distro.commands.reset import command_reset
from proot_distro.commands.login import command_login
from proot_distro.commands.list import command_list
from proot_distro.commands.backup import command_backup
from proot_distro.commands.restore import command_restore
from proot_distro.commands.clear_cache import command_clear_cache
from proot_distro.commands.copy import command_copy
from proot_distro.commands.sync import command_sync
from proot_distro.commands.run import command_run
from proot_distro.commands.build import command_build
from proot_distro.commands.push import command_push
from proot_distro.commands.help import command_help, _HELP_COMMANDS


class _PdArgumentParser(argparse.ArgumentParser):
    _pd_command: "str | None" = None

    def error(self, message: str) -> None:
        msg()
        msg(f"{C['BRED']}Error: {message}{C['RST']}")
        if self._pd_command and self._pd_command in _HELP_COMMANDS:
            _HELP_COMMANDS[self._pd_command]()
        msg()
        sys.exit(1)


def build_parser() -> "_PdArgumentParser":
    parser = _PdArgumentParser(
        prog=PROGRAM_NAME,
        description="Manage Linux proot containers.",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="command")

    # help
    sub.add_parser("help", aliases=["hel", "he", "h"], add_help=False)

    # install
    p_install = sub.add_parser(
        "install", aliases=["add", "i", "in", "ins"], add_help=False
    )
    p_install._pd_command = "install"
    p_install.add_argument("alias", nargs="?", default=None, metavar="IMAGE")
    _p_install_name = p_install.add_mutually_exclusive_group()
    _p_install_name.add_argument("--name", dest="custom_dist_name", metavar="ALIAS")
    _p_install_name.add_argument(
        "--override-alias", dest="custom_dist_name", metavar="ALIAS"
    )
    p_install.add_argument(
        "--architecture", dest="override_arch", metavar="ARCH",
    )
    p_install.add_argument("-h", "--help", action="store_true")

    # remove
    p_remove = sub.add_parser("remove", aliases=["rm"], add_help=False)
    p_remove._pd_command = "remove"
    p_remove.add_argument("alias", nargs="?", default=None)
    p_remove.add_argument("-v", "--verbose", action="store_true")
    p_remove.add_argument("-h", "--help", action="store_true")

    # rename
    p_rename = sub.add_parser("rename", add_help=False)
    p_rename._pd_command = "rename"
    p_rename.add_argument("orig_alias", nargs="?", default=None)
    p_rename.add_argument("new_alias", nargs="?", default=None)
    p_rename.add_argument("-h", "--help", action="store_true")

    # reset
    p_reset = sub.add_parser("reset", add_help=False)
    p_reset._pd_command = "reset"
    p_reset.add_argument("alias", nargs="?", default=None)
    p_reset.add_argument("-h", "--help", action="store_true")

    # login
    p_login = sub.add_parser("login", aliases=["sh"], add_help=False)
    p_login._pd_command = "login"
    p_login.add_argument("alias", nargs="?", default=None)
    p_login.add_argument("--user", default="root")
    _p_login_ports = p_login.add_mutually_exclusive_group()
    _p_login_ports.add_argument(
        "--redirect-ports", dest="redirect_ports", action="store_true"
    )
    _p_login_ports.add_argument(
        "--fix-low-ports", dest="redirect_ports", action="store_true"
    )
    if IS_TERMUX:
        _p_login_isolation = p_login.add_mutually_exclusive_group()
        _p_login_isolation.add_argument("--isolated", action="store_true")
        _p_login_isolation.add_argument("--minimal", action="store_true")
    _p_login_shared_home = p_login.add_mutually_exclusive_group()
    _p_login_shared_home.add_argument(
        "--shared-home", dest="shared_home", action="store_true"
    )
    _p_login_shared_home.add_argument(
        "--termux-home", dest="shared_home", action="store_true"
    )
    p_login.add_argument(
        "--shared-tmp", dest="shared_tmp", action="store_true"
    )
    p_login.add_argument(
        "--shared-x11", dest="shared_x11", action="store_true"
    )
    p_login.add_argument(
        "--bind", action="append", metavar="PATH[:PATH]"
    )
    if IS_TERMUX:
        p_login.add_argument(
            "--no-link2symlink", dest="no_link2symlink", action="store_true"
        )
        p_login.add_argument(
            "--no-sysvipc", dest="no_sysvipc", action="store_true"
        )
        p_login.add_argument(
            "--no-kill-on-exit", dest="no_kill_on_exit", action="store_true"
        )
    p_login.add_argument("--emulator", dest="emulator", metavar="PATH")
    p_login.add_argument("--kernel", metavar="STRING")
    p_login.add_argument("--hostname", metavar="STRING")
    p_login.add_argument("--work-dir", dest="work_dir", metavar="PATH")
    p_login.add_argument("--env", action="append", metavar="VAR=VALUE")
    p_login.add_argument("--get-proot-cmd", dest="get_proot_cmd", action="store_true")
    p_login.add_argument("login_cmd", nargs="*")
    p_login.add_argument("-h", "--help", action="store_true")

    # list
    p_list = sub.add_parser("list", aliases=["li", "ls"], add_help=False)
    p_list._pd_command = "list"
    p_list.add_argument("-h", "--help", action="store_true")

    # backup
    p_backup = sub.add_parser(
        "backup", aliases=["bak", "bkp"], add_help=False
    )
    p_backup._pd_command = "backup"
    p_backup.add_argument("alias", nargs="?", default=None)
    p_backup.add_argument("--output", metavar="FILE")
    p_backup.add_argument(
        "--compress", dest="compression",
        choices=["gzip", "bzip2", "xz", "none"], metavar="TYPE",
    )
    p_backup.add_argument("-v", "--verbose", action="store_true")
    p_backup.add_argument("-h", "--help", action="store_true")

    # restore
    p_restore = sub.add_parser("restore", add_help=False)
    p_restore._pd_command = "restore"
    p_restore.add_argument("archive", nargs="?")
    p_restore.add_argument("-v", "--verbose", action="store_true")
    p_restore.add_argument("-h", "--help", action="store_true")

    # clear-cache
    p_clear = sub.add_parser(
        "clear-cache", aliases=["clear", "cl"], add_help=False
    )
    p_clear._pd_command = "clear-cache"
    p_clear.add_argument("-v", "--verbose", action="store_true")
    p_clear.add_argument("-h", "--help", action="store_true")

    # copy
    p_copy = sub.add_parser("copy", aliases=["cp"], add_help=False)
    p_copy._pd_command = "copy"
    p_copy.add_argument("source", nargs="?", default=None)
    p_copy.add_argument("destination", nargs="?", default=None)
    p_copy.add_argument("-v", "--verbose", action="store_true")
    p_copy.add_argument("-m", "--move", action="store_true")
    p_copy.add_argument("-r", "--recursive", action="store_true")
    p_copy.add_argument("-h", "--help", action="store_true")

    # sync
    p_sync = sub.add_parser("sync", add_help=False)
    p_sync._pd_command = "sync"
    p_sync.add_argument("source", nargs="?", default=None)
    p_sync.add_argument("destination", nargs="?", default=None)
    p_sync.add_argument("-v", "--verbose", action="store_true")
    p_sync.add_argument("--checksum", action="store_true")
    p_sync.add_argument("--delete", action="store_true")
    p_sync.add_argument("-h", "--help", action="store_true")

    # build
    p_build = sub.add_parser("build", add_help=False)
    p_build._pd_command = "build"
    p_build.add_argument("path", nargs="?", default=".", metavar="PATH")
    p_build.add_argument("-f", "--file", dest="dockerfile", metavar="PATH")
    p_build.add_argument(
        "-t", "--tag", dest="tags", action="append",
        default=[], metavar="REF",
    )
    p_build.add_argument(
        "--build-arg", dest="build_args", action="append",
        default=[], metavar="K=V",
    )
    p_build.add_argument(
        "--architecture", dest="override_arch", metavar="ARCH",
    )
    p_build.add_argument(
        "--target", dest="target_stage", metavar="STAGE",
    )
    p_build.add_argument("--emulator", dest="emulator", metavar="PATH")
    p_build.add_argument(
        "--output", dest="outputs", action="append",
        default=[], metavar="FILE",
    )
    p_build.add_argument(
        "--install-as", dest="install_as", metavar="NAME",
    )
    p_build.add_argument(
        "--no-cache", dest="no_cache", action="store_true",
    )
    p_build.add_argument(
        "--pull", dest="force_pull", action="store_true",
    )
    p_build.add_argument("-v", "--verbose", action="store_true")
    p_build.add_argument("-q", "--quiet", action="store_true")
    p_build.add_argument("-h", "--help", action="store_true")

    # push
    p_push = sub.add_parser("push", add_help=False)
    p_push._pd_command = "push"
    p_push.add_argument("image_ref", nargs="?", default=None, metavar="IMAGE")
    p_push.add_argument(
        "--architecture", dest="override_arch", metavar="ARCH",
    )
    p_push.add_argument("-q", "--quiet", action="store_true")
    p_push.add_argument("-h", "--help", action="store_true")

    # run
    p_run = sub.add_parser("run", add_help=False)
    p_run._pd_command = "run"
    p_run.add_argument("alias", nargs="?", default=None)
    p_run.add_argument("--user", default="root")
    p_run.add_argument(
        "--redirect-ports", dest="redirect_ports", action="store_true"
    )
    if IS_TERMUX:
        _p_run_isolation = p_run.add_mutually_exclusive_group()
        _p_run_isolation.add_argument("--isolated", action="store_true")
        _p_run_isolation.add_argument("--minimal", action="store_true")
    _p_run_shared_home = p_run.add_mutually_exclusive_group()
    _p_run_shared_home.add_argument(
        "--shared-home", dest="shared_home", action="store_true"
    )
    _p_run_shared_home.add_argument(
        "--termux-home", dest="shared_home", action="store_true"
    )
    p_run.add_argument(
        "--shared-tmp", dest="shared_tmp", action="store_true"
    )
    p_run.add_argument(
        "--shared-x11", dest="shared_x11", action="store_true"
    )
    p_run.add_argument(
        "--bind", action="append", metavar="PATH[:PATH]"
    )
    if IS_TERMUX:
        p_run.add_argument(
            "--no-link2symlink", dest="no_link2symlink", action="store_true"
        )
        p_run.add_argument(
            "--no-sysvipc", dest="no_sysvipc", action="store_true"
        )
        p_run.add_argument(
            "--no-kill-on-exit", dest="no_kill_on_exit", action="store_true"
        )
    p_run.add_argument("--emulator", dest="emulator", metavar="PATH")
    p_run.add_argument("--kernel", metavar="STRING")
    p_run.add_argument("--hostname", metavar="STRING")
    p_run.add_argument("--work-dir", dest="work_dir", metavar="PATH")
    p_run.add_argument("--env", action="append", metavar="VAR=VALUE")
    p_run.add_argument("--get-proot-cmd", action="store_true")
    p_run.add_argument("-h", "--help", action="store_true")

    return parser


_ALIAS_TO_CANONICAL = {
    "add": "install", "i": "install", "in": "install", "ins": "install",
    "rm": "remove",
    "sh": "login",
    "li": "list", "ls": "list",
    "bak": "backup", "bkp": "backup",
    "clear": "clear-cache", "cl": "clear-cache",
    "cp": "copy",
    "h": "help", "he": "help", "hel": "help",
}

# Maps each canonical command to required (arg_name, error_message) pairs.
_REQUIRED_ARGS = {
    "install": [("alias", "Docker image reference is not specified"
                 " (e.g. 'ubuntu:24.04').")],
    "remove":  [("alias", "container name is not specified.")],
    "rename":  [("orig_alias", "the original container name is not specified."),
                ("new_alias",  "the new container name is not specified.")],
    "reset":   [("alias", "container name is not specified.")],
    "login":   [("alias", "container name is not specified.")],
    "backup":  [("alias", "container name is not specified.")],
    "copy":    [("source",      "source path is not specified."),
                ("destination", "destination path is not specified.")],
    "sync":    [("source",      "source path is not specified."),
                ("destination", "destination path is not specified.")],
    "run":     [("alias", "container name is not specified.")],
    "push":    [("image_ref", "image reference is not specified"
                 " (e.g. 'myrepo/myapp:1.0').")],
}

_COMMAND_HANDLERS = {
    "install":     command_install,
    "remove":      command_remove,
    "rename":      command_rename,
    "reset":       command_reset,
    "login":       command_login,
    "list":        command_list,
    "backup":      command_backup,
    "restore":     command_restore,
    "clear-cache": command_clear_cache,
    "copy":        command_copy,
    "sync":        command_sync,
    "run":         command_run,
    "build":       command_build,
    "push":        command_push,
    "help":        command_help,
}


def _sigquit_to_keyboard_interrupt(_signum, _frame):
    raise KeyboardInterrupt()


def main() -> None:
    # Route SIGQUIT (Ctrl-\) through KeyboardInterrupt so every
    # 'except KeyboardInterrupt' block in the codebase handles it the
    # same as SIGINT (Ctrl-C). The default disposition of SIGQUIT is
    # to terminate the process with a core dump, which would skip
    # progress-bar cleanup, partial-file removal, and "Aborted by
    # user" messaging that the Ctrl-C handlers already provide.
    signal.signal(signal.SIGQUIT, _sigquit_to_keyboard_interrupt)

    # Warn if running as root.
    if os.getuid() == 0:
        msg()
        msg(f"{C['BRED']}Warning: {PROGRAM_NAME} should not be executed as "
            f"root user. Do not send bug reports about messed up Termux "
            f"environment, lost data and bricked devices.{C['RST']}")
        msg()

    # Warn if running inside proot (nested proot).
    try:
        with open(f"/proc/{os.getpid()}/status") as fh:
            for line in fh:
                if line.startswith("TracerPid:"):
                    tracer_pid = int(line.split()[1])
                    if tracer_pid != 0:
                        with open(f"/proc/{tracer_pid}/status") as tfh:
                            for tline in tfh:
                                if tline.startswith("Name:") and "proot" in tline:
                                    msg()
                                    msg(f"{C['BRED']}Error: {PROGRAM_NAME} "
                                        f"should not be executed under "
                                        f"PRoot.{C['RST']}")
                                    msg()
                                    sys.exit(1)
                    break
    except OSError:
        pass

    # Check that proot is installed. `build` and `push` are exempt from
    # this startup probe: `build` may run a Dockerfile with no RUN
    # instructions in pure-Python mode, and `push` only reads from the
    # local manifest/layer cache and uploads to a registry. `build`
    # runs its own check after parsing the Dockerfile and refuses only
    # when RUN (or ONBUILD RUN) is actually present.
    _first_canonical = ""
    if len(sys.argv) >= 2:
        first_arg = sys.argv[1]
        _first_canonical = _ALIAS_TO_CANONICAL.get(first_arg, first_arg)
    if (_first_canonical not in ("build", "push")
            and shutil.which("proot") is None):
        msg()
        msg(f"{C['BRED']}Error: unable to find proot utility.{C['RST']}")
        msg()
        if IS_TERMUX:
            if sys.stdin.isatty():
                sys.stderr.write(
                    f"{C['CYAN']}Would you like to install it now? [y/N] {C['RST']}"
                )
                sys.stderr.flush()
                try:
                    answer = input().strip().lower()
                except (EOFError, KeyboardInterrupt):
                    answer = ""
                if answer in ("y", "yes"):
                    msg()
                    try:
                        subprocess.run(
                            ["pkg", "install", "-y", "-q", "proot"], check=True
                        )
                    except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                        msg()
                        msg(f"{C['BRED']}Error: failed to install proot: "
                            f"{exc}{C['RST']}")
                        msg()
                        sys.exit(1)
                else:
                    msg()
                    msg(f"{C['CYAN']}Install it manually with: "
                        f"{C['GREEN']}pkg install proot{C['RST']}")
                    msg()
                    sys.exit(1)
            else:
                msg(f"{C['CYAN']}Install it with: "
                    f"{C['GREEN']}pkg install proot{C['RST']}")
                msg()
        sys.exit(1)

    if len(sys.argv) < 2 or sys.argv[1] in (
        "-h", "--help", "help", "hel", "he", "h"
    ):
        command_help()
        sys.exit(0)

    raw_args = sys.argv[1:]

    # Intercept subcommand-level --help/-h before argparse runs so that
    # missing required positionals don't produce an error instead of help.
    if len(raw_args) >= 2 and raw_args[1] in ("-h", "--help", "--usage"):
        cmd = _ALIAS_TO_CANONICAL.get(raw_args[0], raw_args[0])
        if cmd in _HELP_COMMANDS:
            _HELP_COMMANDS[cmd]()
            sys.exit(0)

    # Validate the command before argparse runs. An unknown subcommand name
    # causes _SubParsersAction to raise ArgumentError, which parse_known_args
    # routes through self.error() — printing argparse's own message and
    # exiting before our custom error handler is ever reached.
    _first = raw_args[0] if raw_args else None
    if (
        _first is not None
        and not _first.startswith("-")
        and _first not in _COMMAND_HANDLERS
        and _first not in _ALIAS_TO_CANONICAL
    ):
        msg()
        msg(f"{C['BRED']}Error: unknown command "
            f"'{C['YELLOW']}{_first}{C['BRED']}'.{C['RST']}")
        command_help()
        msg()
        sys.exit(1)

    parser = build_parser()
    args, unknown = parser.parse_known_args(raw_args)

    command = args.command
    if command is None:
        msg()
        msg(f"{C['BRED']}Error: unknown command "
            f"'{C['YELLOW']}{raw_args[0]}{C['BRED']}'.{C['RST']}")
        command_help()
        msg()
        sys.exit(1)

    canonical = _ALIAS_TO_CANONICAL.get(command, command)

    # Show per-command help.
    if getattr(args, "help", False):
        if canonical in _HELP_COMMANDS:
            _HELP_COMMANDS[canonical]()
        else:
            command_help()
        sys.exit(0)

    # Check for unrecognized options/arguments. For login and run, args after
    # '--' are passed to the inner command and must not be flagged — re-parse
    # only the portion before '--' to get a clean unknown list.
    check_unknown = unknown
    if canonical in ("login", "run") and "--" in raw_args:
        sep_idx = raw_args.index("--")
        _, check_unknown = parser.parse_known_args(raw_args[:sep_idx])
    if check_unknown:
        bad = check_unknown[0]
        kind = "unrecognized option" if bad.startswith("-") else "unexpected argument"
        msg()
        msg(f"{C['BRED']}Error: {kind}: "
            f"'{C['YELLOW']}{bad}{C['BRED']}'.{C['RST']}")
        if canonical in _HELP_COMMANDS:
            _HELP_COMMANDS[canonical]()
        msg()
        sys.exit(1)

    # Validate required positional arguments.
    for arg_name, error_msg in _REQUIRED_ARGS.get(canonical, []):
        if getattr(args, arg_name, None) is None:
            msg()
            msg(f"{C['BRED']}Error: {error_msg}{C['RST']}")
            if canonical in _HELP_COMMANDS:
                _HELP_COMMANDS[canonical]()
            sys.exit(1)

    # For login, handle the -- separator to split login_cmd.
    if canonical == "login" and "--" in raw_args:
        sep_idx = raw_args.index("--")
        args.login_cmd = raw_args[sep_idx + 1:]
    elif canonical == "login":
        args.login_cmd = []

    # For run, handle the -- separator to split run_args.
    if canonical == "run" and "--" in raw_args:
        sep_idx = raw_args.index("--")
        args.run_args = raw_args[sep_idx + 1:]
    elif canonical == "run":
        args.run_args = []

    configs = {}

    handler = _COMMAND_HANDLERS.get(canonical)
    if handler is None:
        msg()
        msg(f"{C['BRED']}Error: unknown command "
            f"'{C['YELLOW']}{command}{C['BRED']}'.{C['RST']}")
        msg()
        sys.exit(1)

    handler(args, configs)
