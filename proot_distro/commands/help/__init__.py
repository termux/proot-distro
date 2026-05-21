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

# Architecture: Top-level help command. The per-command pages live in
# pages.py as plain data; this module renders the no-args overview page
# and exposes HELP_COMMANDS so cli.py can dispatch a one-page render
# for a single command.

from proot_distro.constants import PROGRAM_NAME, RUNTIME_DIR
from proot_distro.message import C, msg
from proot_distro.commands.help.pages import HELP_PAGES, TOP_COMMANDS
from proot_distro.commands.help.render import (
    commands_block,
    footer,
    paragraph,
    render_page,
    section,
    shell_block,
    term_width,
    usage_line,
)


def _make_help_fn(name):
    def help_fn():
        render_page(HELP_PAGES[name])
    return help_fn


# Map every command name to a zero-arg renderer. Imported by the CLI
# dispatcher so per-command --help calls one entry from this table.
HELP_COMMANDS = {name: _make_help_fn(name) for name in HELP_PAGES}


def command_help(args=None) -> None:
    """Render the top-level help page (no command argument).

    *args* is accepted for signature uniformity with the other
    command_X handlers in the dispatch table but is intentionally
    ignored — the no-args overview page takes no inputs.
    """
    width = term_width()

    section("USAGE")
    usage_line("[COMMAND] [ARGUMENTS]", width)

    section("DESCRIPTION")
    paragraph(
        "PRoot-Distro is a wrapper utility for proot user-space "
        "emulator of chroot, bind --mount and binfmt_misc. This "
        "utility provides a convenient way for working with Linux "
        "containers, leveraging support of Docker registries to "
        "provide distributions of any kind.",
        width,
    )

    section("COMMANDS")
    commands_block(TOP_COMMANDS, width)

    section("GETTING HELP")
    paragraph(
        f"Run '{PROGRAM_NAME} <command> --help' for details on any command.",
        width,
    )

    section("QUICK START")
    paragraph(
        "Usage of generic distribution images is straightforward. "
        "Below is an example for Ubuntu 24.04:",
        width,
    )
    msg()
    shell_block(
        [f"{PROGRAM_NAME} install ubuntu:24.04",
         f"{PROGRAM_NAME} login ubuntu"], width,
    )
    msg()
    paragraph(
        "Some images come preconfigured for specific purposes and "
        "contain built-in startup script. Often this is a case for "
        "server software:",
        width,
    )
    msg()
    shell_block(
        [f"{PROGRAM_NAME} install nextcloud:32",
         f"{PROGRAM_NAME} run --redirect-ports nextcloud"], width,
    )
    msg()
    paragraph(
        "Images that are not officially provided by Docker Hub "
        "require specifying organization or user name:",
        width,
    )
    msg()
    shell_block(
        [f"{PROGRAM_NAME} install termux/termux-docker:aarch64"], width,
    )
    msg()
    paragraph(
        "If you no longer need a specific container, delete it with:",
        width,
    )
    msg()
    shell_block([f"{PROGRAM_NAME} remove ubuntu"], width)
    msg()
    paragraph(
        "You can discover existing images on Docker Hub "
        "(https://hub.docker.com/) or other places on the Internet. "
        "You can also build your own image from a Dockerfile with "
        f"'{PROGRAM_NAME} build'.",
        width,
    )

    section("DATA LOCATION")
    msg(f"  {C['YELLOW']}{RUNTIME_DIR}{C['RST']}")

    section("TROUBLESHOOTING")
    paragraph(
        "If your terminal (theme) does not work well with colors, "
        "set this environment variable:",
        width,
    )
    msg()
    shell_block(["export PD_FORCE_NO_COLORS=true"], width)
    msg()
    paragraph(
        "To pull private Docker/OCI images, set credentials via "
        "PD_DOCKER_AUTH in 'username:password' format before "
        "running the install command:",
        width,
    )
    msg()
    shell_block(
        ["export PD_DOCKER_AUTH=user:password",
         f"{PROGRAM_NAME} install ghcr.io/myorg/private-image:tag"],
        width,
    )
    msg()
    paragraph(
        "If you have issues with proot during login, try these "
        "quick troubleshooting steps:",
        width,
    )
    msg()
    shell_block(
        ["pkg upgrade -y",
         f"PROOT_NO_SECCOMP=1 {PROGRAM_NAME} login <name>"],
        width,
    )
    msg()
    paragraph(
        "Report utility issues to "
        "https://github.com/termux/proot-distro/issues",
        width,
    )

    footer(width)


__all__ = ("command_help", "HELP_COMMANDS")
