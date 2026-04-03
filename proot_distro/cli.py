"""
Proot-Distro - manage proot containers on Termux.

Created by Sylirre <sylirre@termux.dev> for Termux project.
Development assisted by Claude Code (https://claude.ai/code).

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program. If not, see <http://www.gnu.org/licenses/>.
"""
import argparse
import os
import shutil
import subprocess
import sys

from proot_distro.constants import PROGRAM_NAME
from proot_distro.colors import C, msg
from proot_distro.config import discover_configs, _ensure_config, _ensure_all_configs
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
from proot_distro.commands.help import command_help, _HELP_COMMANDS


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=PROGRAM_NAME,
        description="Manage Linux proot containers on Termux.",
        add_help=False,
    )
    parser.add_argument("-h", "--help", action="store_true")
    sub = parser.add_subparsers(dest="command")

    # help
    p_help = sub.add_parser("help", aliases=["hel", "he", "h"], add_help=False)

    # install
    p_install = sub.add_parser("install", aliases=["add", "i", "in", "ins"], add_help=False)
    p_install.add_argument("alias", nargs="?", default=None)
    p_install.add_argument("--name", dest="custom_dist_name", metavar="ALIAS")
    p_install.add_argument("--architecture", dest="override_arch", metavar="ARCH",
                           choices=["aarch64", "arm", "i686", "riscv64", "x86_64"])
    p_install.add_argument("--url", dest="override_url", metavar="URL")
    p_install.add_argument("--checksum", dest="override_checksum", metavar="SHA256")
    p_install.add_argument("--strip-path-components", dest="override_strip",
                           metavar="N", type=int)
    p_install.add_argument("-h", "--help", action="store_true")

    # remove
    p_remove = sub.add_parser("remove", aliases=["rm"], add_help=False)
    p_remove.add_argument("alias", nargs="?", default=None)
    p_remove.add_argument("-v", "--verbose", action="store_true")
    p_remove.add_argument("-h", "--help", action="store_true")

    # rename
    p_rename = sub.add_parser("rename", aliases=["mv"], add_help=False)
    p_rename.add_argument("orig_alias", nargs="?", default=None)
    p_rename.add_argument("new_alias", nargs="?", default=None)
    p_rename.add_argument("-h", "--help", action="store_true")

    # reset
    p_reset = sub.add_parser("reset", add_help=False)
    p_reset.add_argument("alias", nargs="?", default=None)
    p_reset.add_argument("-h", "--help", action="store_true")

    # login
    p_login = sub.add_parser("login", aliases=["sh"], add_help=False)
    p_login.add_argument("alias", nargs="?", default=None)
    p_login.add_argument("--user", default="root")
    p_login.add_argument("--redirect-ports", dest="redirect_ports", action="store_true")
    p_login.add_argument("--isolated", action="store_true")
    p_login.add_argument("--termux-home", dest="termux_home", action="store_true")
    p_login.add_argument("--shared-tmp", dest="shared_tmp", action="store_true")
    p_login.add_argument("--bind", action="append", metavar="PATH[:PATH]")
    p_login.add_argument("--no-link2symlink", dest="no_link2symlink", action="store_true")
    p_login.add_argument("--no-sysvipc", dest="no_sysvipc", action="store_true")
    p_login.add_argument("--no-kill-on-exit", dest="no_kill_on_exit", action="store_true")
    p_login.add_argument("--no-arch-warning", dest="no_arch_warning", action="store_true")
    p_login.add_argument("--emulator", dest="emulator", metavar="PATH")
    p_login.add_argument("--kernel", metavar="STRING")
    p_login.add_argument("--hostname", metavar="STRING")
    p_login.add_argument("--work-dir", dest="work_dir", metavar="PATH")
    p_login.add_argument("--env", action="append", metavar="VAR=VALUE")
    p_login.add_argument("login_cmd", nargs="*")
    p_login.add_argument("-h", "--help", action="store_true")

    # list
    p_list = sub.add_parser("list", aliases=["li", "ls"], add_help=False)
    p_list.add_argument("--detailed", action="store_true")
    p_list.add_argument("-h", "--help", action="store_true")

    # backup
    p_backup = sub.add_parser("backup", aliases=["bak", "bkp"], add_help=False)
    p_backup.add_argument("alias", nargs="?", default=None)
    p_backup.add_argument("--output", metavar="FILE")
    p_backup.add_argument("--compression", dest="compression",
                          choices=["gzip", "bzip2", "xz", "none"], metavar="TYPE")
    p_backup.add_argument("-h", "--help", action="store_true")

    # restore
    p_restore = sub.add_parser("restore", add_help=False)
    p_restore.add_argument("archive", nargs="?")
    p_restore.add_argument("-h", "--help", action="store_true")

    # clear-cache
    p_clear = sub.add_parser("clear-cache", aliases=["clear", "cl"], add_help=False)
    p_clear.add_argument("-h", "--help", action="store_true")

    # copy
    p_copy = sub.add_parser("copy", aliases=["cp"], add_help=False)
    p_copy.add_argument("source", nargs="?", default=None)
    p_copy.add_argument("destination", nargs="?", default=None)
    p_copy.add_argument("-v", "--verbose", action="store_true")
    p_copy.add_argument("-m", "--move", action="store_true")
    p_copy.add_argument("-h", "--help", action="store_true")

    return parser


_ALIAS_TO_CANONICAL = {
    "add": "install", "i": "install", "in": "install", "ins": "install",
    "rm": "remove",
    "mv": "rename",
    "sh": "login",
    "li": "list", "ls": "list",
    "bak": "backup", "bkp": "backup",
    "clear": "clear-cache", "cl": "clear-cache",
    "cp": "copy",
    "h": "help", "he": "help", "hel": "help",
}

# (canonical_command) -> list of (arg_name, error_message) pairs checked in order.
_REQUIRED_ARGS = {
    "install": [("alias",      "distribution alias is not specified.")],
    "remove":  [("alias",      "distribution alias is not specified.")],
    "rename":  [("orig_alias", "the original alias of distribution is not specified."),
                ("new_alias",  "the new alias of distribution is not specified.")],
    "reset":   [("alias",      "distribution alias is not specified.")],
    "login":   [("alias",      "distribution alias is not specified.")],
    "backup":  [("alias",      "distribution alias is not specified.")],
    "copy":    [("source",      "source path is not specified."),
                ("destination", "destination path is not specified.")],
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
    "help":        command_help,
}


def main() -> None:
    # Warn if running as root.
    if os.getuid() == 0:
        msg()
        msg(f"{C['BRED']}Warning: {PROGRAM_NAME} should not be executed as root user. "
            f"Do not send bug reports about messed up Termux environment, lost data and bricked devices.{C['RST']}")
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
                                    msg(f"{C['BRED']}Error: {PROGRAM_NAME} should not be executed under PRoot.{C['RST']}")
                                    msg()
                                    sys.exit(1)
                    break
    except OSError:
        pass

    # Check that proot is installed.
    if shutil.which("proot") is None:
        msg()
        msg(f"{C['BRED']}Error: unable to find proot utility.{C['RST']}")
        msg()
        if sys.stdin.isatty():
            sys.stderr.write(f"{C['CYAN']}Would you like to install it now? [y/N] {C['RST']}")
            sys.stderr.flush()
            try:
                answer = input().strip().lower()
            except (EOFError, KeyboardInterrupt):
                answer = ""
            if answer in ("y", "yes"):
                msg()
                try:
                    subprocess.run(["pkg", "install", "-y", "-q", "proot"], check=True)
                except (subprocess.CalledProcessError, FileNotFoundError) as exc:
                    msg()
                    msg(f"{C['BRED']}Error: failed to install proot: {exc}{C['RST']}")
                    msg()
                    sys.exit(1)
            else:
                msg()
                msg(f"{C['CYAN']}Install it manually with: {C['GREEN']}pkg install proot{C['RST']}")
                msg()
                sys.exit(1)
        else:
            msg(f"{C['CYAN']}Install it with: {C['GREEN']}pkg install proot{C['RST']}")
            msg()
            sys.exit(1)

    if len(sys.argv) < 2 or sys.argv[1] in ("-h", "--help", "help", "hel", "he", "h"):
        command_help()
        sys.exit(0)

    raw_args = sys.argv[1:]

    # Intercept subcommand-level --help/-h before argparse runs, so that
    # missing required positionals don't produce an error instead of help.
    if len(raw_args) >= 2 and raw_args[1] in ("-h", "--help"):
        cmd = _ALIAS_TO_CANONICAL.get(raw_args[0], raw_args[0])
        if cmd in _HELP_COMMANDS:
            _HELP_COMMANDS[cmd]()
            sys.exit(0)

    parser = build_parser()
    args, _ = parser.parse_known_args(raw_args)

    command = args.command
    if command is None:
        msg()
        msg(f"{C['BRED']}Error: unknown command '{C['YELLOW']}{raw_args[0]}{C['BRED']}'.{C['RST']}")
        msg()
        msg(f"{C['CYAN']}View supported commands by: {C['GREEN']}{PROGRAM_NAME} help{C['RST']}")
        msg()
        sys.exit(1)

    # Resolve aliases.
    canonical = _ALIAS_TO_CANONICAL.get(command, command)

    # Show per-command help.
    if getattr(args, "help", False):
        if canonical in _HELP_COMMANDS:
            _HELP_COMMANDS[canonical]()
        else:
            command_help(args, {})
        sys.exit(0)

    # Validate required positional arguments and show command help on missing ones.
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

    # Populate PD_CONFIGS_DIR on demand before discovery.
    if canonical in ("list", "help"):
        _ensure_all_configs()
    else:
        for _attr in ("alias", "orig_alias"):
            _val = getattr(args, _attr, None)
            if _val:
                _ensure_config(_val)

    configs = discover_configs()

    handler = _COMMAND_HANDLERS.get(canonical)
    if handler is None:
        msg()
        msg(f"{C['BRED']}Error: unknown command '{C['YELLOW']}{command}{C['BRED']}'.{C['RST']}")
        msg()
        sys.exit(1)

    handler(args, configs)
