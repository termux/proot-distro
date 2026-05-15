#
# Proot-Distro - manage proot containers on Termux.
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

# Architecture: Adaptive help renderer. Help content lives in _HELP_PAGES as
# plain Python data; _render_page formats it against the detected terminal
# width. Layout switches at _NARROW_BREAKPOINT (60 cols): below it, option
# names and descriptions stack vertically — fitting phones running Termux;
# above it, a two-column grid keeps the page scannable on tablets and
# laptops. Width is clamped to [_MIN_WIDTH, _MAX_WIDTH] so output stays
# readable on narrow PTYs and not painfully long on ultra-wide ones.

import os
import shutil
import textwrap

from proot_distro.constants import (
    PROGRAM_NAME,
    PROGRAM_VERSION,
    RUNTIME_DIR,
    TERMUX_APP_PACKAGE,
)
from proot_distro.colors import C, msg


# ---------------------------------------------------------------------------
# Layout constants and width detection
# ---------------------------------------------------------------------------

_MIN_WIDTH = 32
_MAX_WIDTH = 92
_NARROW_BREAKPOINT = 60

_RULE_DOUBLE = "═"
_RULE_SINGLE = "─"
_BULLET = "▸"
_PROMPT = "$"


def _term_width() -> int:
    # Help text is written to stderr, so prefer stderr's reported size first
    # and fall back to stdout/stdin and finally COLUMNS for redirected runs.
    for fd in (2, 1, 0):
        try:
            cols = os.get_terminal_size(fd).columns
        except (OSError, ValueError):
            continue
        if cols > 0:
            return max(_MIN_WIDTH, min(_MAX_WIDTH, cols))
    try:
        cols = int(os.environ.get("COLUMNS", "0"))
    except ValueError:
        cols = 0
    if cols > 0:
        return max(_MIN_WIDTH, min(_MAX_WIDTH, cols))
    try:
        cols = shutil.get_terminal_size((72, 24)).columns
    except (OSError, ValueError):
        cols = 72
    return max(_MIN_WIDTH, min(_MAX_WIDTH, cols))


def _wrap(text: str, width: int) -> list:
    width = max(4, width)
    lines = textwrap.wrap(
        text,
        width=width,
        break_long_words=False,
        break_on_hyphens=False,
    )
    return lines or [""]


# ---------------------------------------------------------------------------
# Rendering primitives
# ---------------------------------------------------------------------------

def _hrule(width: int, char: str, color: str = None) -> str:
    return f"{color or C['CYAN']}{char * width}{C['RST']}"


def _banner(title: str, width: int) -> None:
    msg()
    msg(_hrule(width, _RULE_DOUBLE, C["BYELLOW"]))
    label = f"v{PROGRAM_VERSION}"
    # 2 left pad, 4 between title and version, 2 right pad
    if width >= 2 + len(title) + 4 + len(label) + 2:
        gap = width - 2 - len(title) - len(label) - 2
        msg(
            f"  {C['BCYAN']}{title}{C['RST']}"
            f"{' ' * gap}"
            f"{C['ICYAN']}{label}{C['RST']}  "
        )
    else:
        msg(f"  {C['BCYAN']}{title}{C['RST']}")
    msg(_hrule(width, _RULE_DOUBLE, C["BYELLOW"]))


def _section(label: str) -> None:
    msg()
    msg(f"{C['UBCYAN']}{label}{C['RST']}")
    msg()


def _paragraph(text: str, width: int, indent: int = 2,
               color: str = None) -> None:
    color = color or C["CYAN"]
    pad = " " * indent
    avail = max(8, width - indent)
    for i, para in enumerate(text.split("\n\n")):
        if i > 0:
            msg()
        for line in _wrap(para, avail):
            msg(f"{pad}{color}{line}{C['RST']}")


def _usage_line(usage: str, width: int) -> None:
    parts = usage.split(" ", 1)
    sub = parts[0]
    rest = parts[1] if len(parts) > 1 else ""
    head = (f"  {C['BGREEN']}{PROGRAM_NAME}{C['RST']}"
            f" {C['UGREEN']}{sub}{C['RST']}")
    head_visible = 2 + len(PROGRAM_NAME) + 1 + len(sub)
    if not rest:
        msg(head)
        return
    if head_visible + 1 + len(rest) <= width:
        msg(f"{head} {C['CYAN']}{rest}{C['RST']}")
        return
    msg(head)
    cont = "    "
    for line in _wrap(rest, width - len(cont)):
        msg(f"{cont}{C['CYAN']}{line}{C['RST']}")


def _aliases_block(aliases) -> None:
    sep = f"{C['CYAN']}, {C['RST']}"
    parts = [f"{C['UGREEN']}{a}{C['RST']}" for a in aliases]
    msg(f"  {C['CYAN']}Aliases:{C['RST']} {sep.join(parts)}")


def _options_stacked(options, width: int) -> None:
    last = len(options) - 1
    for i, (name, desc) in enumerate(options):
        msg(f"  {C['GREEN']}{name}{C['RST']}")
        _paragraph(desc, width, indent=4)
        if i != last:
            msg()


def _options_block(options, width: int) -> None:
    if not options:
        return
    if width < _NARROW_BREAKPOINT:
        _options_stacked(options, width)
        return
    longest = max(len(name) for name, _ in options)
    opt_col = min(longest, max(16, width // 3))
    desc_col = width - opt_col - 4
    if desc_col < 24:
        _options_stacked(options, width)
        return
    cont = " " * (2 + opt_col + 2)
    last = len(options) - 1
    for i, (name, desc) in enumerate(options):
        wrapped = _wrap(desc, desc_col)
        if len(name) <= opt_col:
            head = (
                f"  {C['GREEN']}{name}{C['RST']}"
                f"{' ' * (opt_col - len(name))}  "
                f"{C['CYAN']}{wrapped[0]}{C['RST']}"
            )
            msg(head)
            for line in wrapped[1:]:
                msg(f"{cont}{C['CYAN']}{line}{C['RST']}")
        else:
            msg(f"  {C['GREEN']}{name}{C['RST']}")
            for line in wrapped:
                msg(f"{cont}{C['CYAN']}{line}{C['RST']}")
        if i != last:
            msg()


def _commands_block(commands, width: int) -> None:
    """Like _options_block, but each entry may carry a trailing red warning.

    Each entry is (name, description) or (name, description, warning).
    """
    if not commands:
        return
    longest = max(len(entry[0]) for entry in commands)
    name_col = min(longest, max(12, width // 4))
    desc_col = width - name_col - 4
    narrow = width < _NARROW_BREAKPOINT or desc_col < 24

    if narrow:
        last = len(commands) - 1
        for i, entry in enumerate(commands):
            name = entry[0]
            desc = entry[1]
            warn = entry[2] if len(entry) > 2 else None
            msg(f"  {C['GREEN']}{name}{C['RST']}")
            _paragraph(desc, width, indent=4)
            if warn:
                msg(f"    {C['RED']}{warn}{C['RST']}")
            if i != last:
                msg()
        return

    cont = " " * (2 + name_col + 2)
    for entry in commands:
        name = entry[0]
        desc = entry[1]
        warn = entry[2] if len(entry) > 2 else None
        wrapped = _wrap(desc, desc_col)
        head = (
            f"  {C['GREEN']}{name}{C['RST']}"
            f"{' ' * (name_col - len(name))}  "
            f"{C['CYAN']}{wrapped[0]}{C['RST']}"
        )
        if warn and len(wrapped) == 1 and \
                len(wrapped[0]) + 1 + len(warn) <= desc_col:
            msg(f"{head} {C['RED']}{warn}{C['RST']}")
            continue
        msg(head)
        for line in wrapped[1:]:
            msg(f"{cont}{C['CYAN']}{line}{C['RST']}")
        if warn:
            msg(f"{cont}{C['RED']}{warn}{C['RST']}")


def _shell_block(examples, width: int) -> None:
    avail = max(12, width - 4)
    for ex in examples:
        wrapped = _wrap(ex, avail)
        for i, line in enumerate(wrapped):
            if i == 0:
                msg(
                    f"  {C['YELLOW']}{_PROMPT}{C['RST']} "
                    f"{C['GREEN']}{line}{C['RST']}"
                )
            else:
                msg(f"    {C['GREEN']}{line}{C['RST']}")


def _bullets_block(bullets, width: int) -> None:
    if not bullets:
        return
    longest = max(len(label) for label, _ in bullets)
    name_col = min(longest, max(16, width // 3))
    desc_col = width - 4 - name_col - 2
    narrow = width < _NARROW_BREAKPOINT or desc_col < 16

    if narrow:
        for label, comment in bullets:
            msg(
                f"  {C['CYAN']}{_BULLET}{C['RST']} "
                f"{C['YELLOW']}{label}{C['RST']}"
            )
            if comment:
                _paragraph(f"({comment})", width, indent=6)
        return

    cont = " " * (4 + name_col + 2)
    for label, comment in bullets:
        pad = " " * max(0, name_col - len(label))
        if comment:
            wrapped = _wrap(f"({comment})", desc_col)
            msg(
                f"  {C['CYAN']}{_BULLET}{C['RST']} "
                f"{C['YELLOW']}{label}{C['RST']}{pad}  "
                f"{C['CYAN']}{wrapped[0]}{C['RST']}"
            )
            for line in wrapped[1:]:
                msg(f"{cont}{C['CYAN']}{line}{C['RST']}")
        else:
            msg(
                f"  {C['CYAN']}{_BULLET}{C['RST']} "
                f"{C['YELLOW']}{label}{C['RST']}"
            )


def _footer(width: int) -> None:
    msg()
    msg(_hrule(width, _RULE_SINGLE, C["CYAN"]))
    _paragraph(
        f"Proot-Distro version '{PROGRAM_VERSION}' by Termux (@sylirre).",
        width, indent=0, color=C["ICYAN"],
    )
    msg()


# ---------------------------------------------------------------------------
# Page renderer
# ---------------------------------------------------------------------------

def _render_page(page: dict, command_name: str) -> None:
    width = _term_width()
    #_banner(f"{PROGRAM_NAME} {command_name}", width)

    if page.get("usage"):
        _section("USAGE")
        _usage_line(page["usage"], width)
        if page.get("aliases"):
            msg()
            _aliases_block(page["aliases"])

    if page.get("summary"):
        _section("DESCRIPTION")
        _paragraph(page["summary"], width)

    if page.get("options"):
        _section("OPTIONS")
        _options_block(page["options"], width)

    if page.get("examples"):
        _section("EXAMPLES")
        _shell_block(page["examples"], width)

    for block in page.get("footer", []):
        title = block.get("title")
        if title:
            _section(title)
        intro = block.get("intro")
        if intro:
            _paragraph(intro, width)
        bullets = block.get("bullets")
        if bullets:
            if intro:
                msg()
            _bullets_block(bullets, width)
        examples = block.get("examples")
        if examples:
            if intro or bullets:
                msg()
            _shell_block(examples, width)

    _footer(width)


# ---------------------------------------------------------------------------
# Per-command help data
# ---------------------------------------------------------------------------

_HELP_PAGES = {
    "backup": {
        "usage": "backup [OPTIONS] CONTAINER",
        "aliases": ("bak", "bkp"),
        "summary": (
            "Back up a specified container into a TAR archive. "
            "Compression is determined by the output file extension or "
            "by the --compress option. Output to stdout is "
            "uncompressed by default."
        ),
        "options": [
            ("--help", "Show this help."),
            ("--compress [TYPE]",
             "Force a specific compression algorithm, overriding the "
             "file extension. Supported values: gzip, bzip2, xz, none."),
            ("--output [FILE]",
             "Write the archive to FILE instead of stdout. When "
             "--compress is not given, compression is inferred from "
             "the file extension like tar.gz or txz."),
            ("--verbose",
             "Log each file name as it is added to the archive."),
        ],
        "examples": [
            f"{PROGRAM_NAME} backup ubuntu --output ~/ubuntu.tar.xz",
        ],
    },

    "clear-cache": {
        "usage": "clear-cache",
        "aliases": ("clear", "cl"),
        "summary": (
            "Remove all files from downloads cache (e.g. Docker image "
            "layers)."
        ),
        "options": [
            ("--help", "Show this help."),
            ("--verbose", "Log each removed file."),
        ],
    },

    "copy": {
        "usage": "copy [OPTIONS] [DIST:]SRC [DIST:]DEST",
        "aliases": ("cp",),
        "summary": (
            "Copy files between the host filesystem and a proot "
            "container. Both source and destination may be a local "
            "path or a 'container:path' reference."
        ),
        "options": [
            ("--help", "Show this help."),
            ("--move",
             "Delete source file after a successful copy."),
            ("--recursive", "Recursive mode for copying directories."),
            ("--verbose", "Log each copied file."),
        ],
        "examples": [
            f"{PROGRAM_NAME} copy ./file.txt ubuntu:/root/file.txt",
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    "Directories '.' or '..' are only accepted as "
                    "source, not as destination. Glob patterns are "
                    "not supported."
                ),
            },
        ],
    },

    "install": {
        "usage": "install [OPTIONS] (IMAGE:TAG or /path/to/roofs.tar)",
        "aliases": ("add", "i", "in", "ins"),
        "summary": (
            "Create a proot container from a given source: Docker image, "
            "a local OCI image archive or plain rootfs tarball."
            "\n\n"
            "Installation from Docker image require specifying a reference, "
            "for example 'ubuntu:24.04'. Official images can be specified by "
            "name alone ('ubuntu'), while user images require the "
            "'user/image' form. If no tag (version) specified, the 'latest' "
            "will be used instead."
            "\n\n"
            "By default Docker images will be pulled from Docker Hub. Custom "
            "registry needs to be specified as part of image reference. "
            "Example: 'ghcr.io/foo/bar:tag'."
            "\n\n"
            "Layers are cached locally and reused on subsequent "
            "installs of the same image."
            "\n\n"
            "Container name is being determined from name of Docker image "
            "or rootfs archive file. To be able install multiple instances "
            "of same distribution, you need to override name using a command "
            "line option."
            "\n\n"
            "It is possible to install distribution with architecture "
            "that differs from your host CPU. In such cases you will need "
            "a QEMU user mode emulator to be able run it."
        ),
        "options": [
            ("--help", "Show this help."),
            ("--name [NAME]",
             "Set a custom name for the container. Must start with "
             "alphanumeric character and then may contain only latin "
             "letters, numbers and special symbols dot, minus, underscore. "
             "Default equals to image name without tag and registry prefix."),
            ("--architecture [ARCH]",
             "Override the target CPU architecture. Accepts native "
             "names (aarch64, arm, i686, riscv64, x86_64) or Docker "
             "platform strings (linux/arm64, linux/amd64, linux/arm/v7, "
             "linux/386, linux/riscv64)."),
        ],
        "examples": [
            f"{PROGRAM_NAME} install ubuntu:24.04",
            f"{PROGRAM_NAME} install debian --architecture x86_64",
            f"{PROGRAM_NAME} install ~/linuxfromscratch.tgz --name lfs"
        ],
    },

    "list": {
        "usage": "list",
        "aliases": ("li", "ls"),
        "summary": "List all installed proot containers.",
        "options": [
            ("--help", "Show this help."),
        ],
    },

    "login": {
        "usage": "login [OPTIONS] CONTAINER [-- COMMAND]",
        "aliases": ("sh",),
        "summary": (
            "Start interactive shell configured for a given account "
            "configured in /etc/passwd. Alternatively user can specify "
            "a custom command to use instead of default shell after "
            "command line separator ('--')."
            "\n\n"
            "By default container is not isolated from the host file"
            "system. It is highly discouraged to run destructive commands "
            "unless isolated mode enabled."
        ),
        "options": [
            ("--help", "Show this help."),
            ("--user [USER]", "Switch to USER instead of root."),
            ("--redirect-ports",
             "Replace privileged port bindings with higher port numbers "
              "(22 -> 2022, 80 -> 2080, etc). Port shift offset is "
              "hardcoded into proot executable itself and can't be "
              f"configured through {PROGRAM_NAME}."),
            ("--isolated",
             "Enable Isolated Mode. No host file system bindings created "
             "unless using QEMU user mode emulation or user manually "
             "requested specific directories to be bound."),
            ("--minimal",
             "Enable Isolated Mode with bare mimimum proot configuration. "
             "Only /dev, /proc and /sys are bound. All proot extensions "
             "except link2symlink are disabled. No /proc system data "
             "workarounds, no kernel release override. Specific features "
             "may only be enabled through command line options. Could show "
             "higher performance than in other modes."),
            ("--termux-home",
             "Bind Termux home directory into the container. Takes "
             "priority over Isolated Mode. Already included in default mode."),
            ("--shared-tmp",
             "Bind Termux tmp directory to /tmp. Takes priority over "
             "Isolated Mode. Already included in default mode."),
            ("--shared-x11",
             "Bind Termux X11 socket directory to /tmp/.X11-unix. "
             "Takes priority over Isolated Mode. Inherited by --shared-tmp. "
             "Already included in default mode."),
            ("--bind [SRC:DEST]",
             "Custom filesystem binding. Can be specified multiple "
             "times. Takes priority over Isolated Mode."),
            ("--no-link2symlink",
             "Disable hardlink emulation by proot. Recommended only for "
             "devices with SELinux in permissive mode."),
            ("--no-sysvipc",
             "Disable System V IPC emulation by proot. Recommended only "
             "for devices where kernel has this feature enabled and "
             "SELinux set to permissive mode."),
            ("--no-kill-on-exit",
             "Hang indefinitely until all session processes exit."),
            ("--emulator [FILE]",
             "Override the QEMU emulator binary for cross-arch "
             "execution. Only QEMU user mode and Blink emulators are "
             "supported. FILE must be executable."),
            ("--kernel [TEXT]",
             "Customize the kernel release string reported by uname."),
            ("--hostname [TEXT]", "Customize the system hostname."),
            ("--work-dir [PATH]", "Set the initial working directory."),
            ("--env VAR=VALUE",
             "Set an environment variable. Can be specified multiple "
             "times."),
            ("--get-proot-cmd",
             "Print the fully assembled proot command line and exit "
             "without running it. The output is ready to copy and "
             "paste into a terminal."),
        ],
        "footer": [
            {
                "title": "HOST BINDINGS",
                "intro": (
                    "Without --isolated, the following host paths "
                    "are bound inside the container:"
                ),
                "bullets": [
                    ("/apex", None),
                    ("/data/dalvik-cache", None),
                    (f"/data/data/{TERMUX_APP_PACKAGE}", None),
                    ("/linkerconfig/ld.config.txt", None),
                    ("/linkerconfig/com.android.art/ld.config.txt", None),
                    ("/odm", None),
                    ("/product", None),
                    ("/sdcard", None),
                    ("/storage/emulated/0", None),
                    ("/storage/self/primary", None),
                    ("/system", None),
                    ("/system_ext", None),
                    ("/vendor", None),
                ],
            },
            {
                "title": "NOTES",
                "intro": (
                    "If host utilities like termux-api do not work, "
                    "ensure that PATH includes Termux bin directory as "
                    "well as special environment variables such as "
                    "ANDROID_ART_ROOT, ANDROID_DATA, ANDROID_I18N_ROOT, "
                    "ANDROID_ROOT, ANDROID_TZDATA_ROOT, BOOTCLASSPATH, "
                    "EXTERNAL_STORAGE. Valid values can be retrieved "
                    "through Termux shell."
                    "\n\n"
                    "PRoot-Distro does not guarantee that everything "
                    "inside given distribution will work flawlessly "
                    "and is not responsible for that. Thus it is not "
                    "possible to satisfy requirements of utilities "
                    "needing real root privileges or specific Linux "
                    "kernel features like namespaces."
                    "\n\n"
                    "Devices with ARMv9 CPUs require QEMU user mode "
                    "emulator to be able execute 32-bit programs because "
                    "this architecture no longer include necessary "
                    "instruction set."
                ),
            },
        ],
    },

    "remove": {
        "usage": "remove [OPTIONS] CONTAINER",
        "aliases": ("rm",),
        "summary": (
            "Permanently delete the specified proot container. "
            "No confirmation is requested, be careful."
        ),
        "options": [
            ("--help", "Show this help."),
            ("--verbose", "Log each deleted file."),
        ],
    },

    "rename": {
        "usage": "rename OLDNAME NEWNAME",
        "summary": "Rename the installed proot container.",
        "options": [
            ("--help", "Show this help."),
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    "Renaming updates all proot link2symlink "
                    "entries inside the container, which may take a "
                    "while for large rootfs trees. For data integrity "
                    "reasons user may not terminate process by CTRL-C."
                ),
            },
        ],
    },

    "reset": {
        "usage": "reset CONTAINER",
        "summary": (
            "Rebuild the specified container from scratch using the "
            "stored Docker image manifest. All current data inside "
            "the container will be lost."
            "\n\n"
            "Works only with containers created from Docker images."
        ),
        "options": [
            ("--help", "Show this help."),
        ],
    },

    "restore": {
        "usage": "restore [OPTIONS] [BACKUP_FILE]",
        "summary": (
            "Restore container from a backup archive. When backup file "
            "is not specified, archive data is read from stdin."
        ),
        "options": [
            ("--help", "Show this help."),
            ("--verbose", "Log each extracted file."),
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    "Compression is detected automatically from the "
                    "file header. Supported: gzip, bzip2, xz, "
                    "uncompressed tar. Applies to both file and "
                    "stdin input."
                ),
            },
        ],
    },

    "run": {
        "usage": "run [OPTIONS] CONTAINER [-- ARG ...]",
        "summary": (
            "Run the Entrypoint and/or Cmd defined in the "
            "container's Docker image manifest. Arguments given "
            "after '--' are appended to Entrypoint (replacing the "
            "image-defined Cmd). If neither Entrypoint nor Cmd is "
            "defined and no arguments are given, an error is "
            "reported."
            "\n\n"
            "Primarily intended to be used with server images."
        ),
        "options": [
            ("--help", "Show this help."),
            ("--user [USER]", "Switch to USER instead of root."),
            ("--redirect-ports",
             "Replace privileged port bindings with higher port numbers "
              "(22 -> 2022, 80 -> 2080, etc). Port shift offset is "
              "hardcoded into proot executable itself and can't be "
              f"configured through {PROGRAM_NAME}."),
            ("--isolated",
             "Enable Isolated Mode. No host file system bindings created "
             "unless using QEMU user mode emulation or user manually "
             "requested specific directories to be bound."),
            ("--minimal",
             "Enable Isolated Mode with bare mimimum proot configuration. "
             "Only /dev, /proc and /sys are bound. All proot extensions "
             "except link2symlink are disabled. No /proc system data "
             "workarounds, no kernel release override. Specific features "
             "may only be enabled through command line options. Could show "
             "higher performance than in other modes."),
            ("--termux-home",
             "Bind Termux home directory into the container. Takes "
             "priority over Isolated Mode. Already included in default mode."),
            ("--shared-tmp",
             "Bind Termux tmp directory to /tmp. Takes priority over "
             "Isolated Mode. Already included in default mode."),
            ("--shared-x11",
             "Bind Termux X11 socket directory to /tmp/.X11-unix. "
             "Takes priority over Isolated Mode. Inherited by --shared-tmp. "
             "Already included in default mode."),
            ("--bind [SRC:DEST]",
             "Custom filesystem binding. Can be specified multiple "
             "times. Takes priority over Isolated Mode."),
            ("--no-link2symlink",
             "Disable hardlink emulation by proot. Recommended only for "
             "devices with SELinux in permissive mode."),
            ("--no-sysvipc",
             "Disable System V IPC emulation by proot. Recommended only "
             "for devices where kernel has this feature enabled and "
             "SELinux set to permissive mode."),
            ("--no-kill-on-exit",
             "Hang indefinitely until all session processes exit."),
            ("--emulator [FILE]",
             "Override the QEMU emulator binary for cross-arch "
             "execution. Only QEMU user mode and Blink emulators are "
             "supported. FILE must be executable."),
            ("--kernel [TEXT]",
             "Customize the kernel release string reported by uname."),
            ("--hostname [TEXT]", "Customize the system hostname."),
            ("--work-dir [PATH]", "Set the initial working directory."),
            ("--env VAR=VALUE",
             "Set an environment variable. Can be specified multiple "
             "times."),
            ("--get-proot-cmd",
             "Print the fully assembled proot command line and exit "
             "without running it. The output is ready to copy and "
             "paste into a terminal."),
        ],
        "examples": [
            f"{PROGRAM_NAME} run nextcloud --redirect-ports",
            f"{PROGRAM_NAME} run ubuntu --isolated -- /bin/echo hi",
        ],
        "footer": [
            {
                "title": "NOTES",
                "intro": (
                    "PRoot-Distro does not guarantee that everything "
                    "inside given distribution will work flawlessly "
                    "and is not responsible for that. Thus it is not "
                    "possible to satisfy requirements of utilities "
                    "needing real root privileges or specific Linux "
                    "kernel features like namespaces."
                    "\n\n"
                    "Devices with ARMv9 CPUs require QEMU user mode "
                    "emulator to be able execute 32-bit programs because "
                    "this architecture no longer include necessary "
                    "instruction set."
                ),
            },
        ],
    },

    "sync": {
        "usage": "sync [OPTIONS] [DIST:]SRC [DIST:]DEST",
        "summary": (
            "Efficiently synchronize directory between host and container "
            "by copying only modified files and deleting those which "
            "absent in the source. Files compared by size and modification "
            "timestamp, however it is possible to use more strict "
            "verification by checksum."
            "\n\n"
            "Both source and destination may be a local path or a "
            "'container:path' reference."
        ),
        "options": [
            ("--help", "Show this help."),
            ("--checksum",
             "Compare files by size and CRC32 checksum instead of "
             "size and modification time. Slower but with high precision."),
            ("--delete",
             "After syncing, remove destination files and "
             "directories that have no counterpart in the source. "
             "Only effective when source is a directory."),
            ("--verbose", "Log each synced or deleted entry."),
        ],
        "examples": [
            f"{PROGRAM_NAME} sync ./dotfiles/ ubuntu:/root/",
            f"{PROGRAM_NAME} sync --delete ./app/ ubuntu:/opt/app/"
        ],
    },
}


def _make_help_fn(name):
    def help_fn():
        _render_page(_HELP_PAGES[name], name)
    return help_fn


_HELP_COMMANDS = {name: _make_help_fn(name) for name in _HELP_PAGES}


# ---------------------------------------------------------------------------
# Top-level help
# ---------------------------------------------------------------------------

_TOP_COMMANDS = [
    ("help", "Show this help."),
    ("install", "Install distribution from OCI image or rootfs archive."),
    ("list", "List created containers."),
    ("login", "Start interactive shell inside a container."),
    ("run", "Run container entrypoint in server or distroless images."),
    ("remove", "Delete a container.", "Destroys data!"),
    ("rename", "Rename a container."),
    ("reset", "Reinstall a container from scratch.", "Destroys data!"),
    ("backup", "Save container as a TAR archive."),
    ("restore", "Restore container from a TAR archive.", "Destroys data!"),
    ("clear-cache", "Delete cached downloads."),
    ("copy", "Copy files from/to container."),
    ("sync", "Sync files from/to container."),
]


def command_help(args=None, configs=None) -> None:  # noqa: ARG001
    width = _term_width()
    #_banner(PROGRAM_NAME, width)

    _section("USAGE")
    _usage_line("[COMMAND] [ARGUMENTS]", width)

    _section("DESCRIPTION")
    _paragraph(
        "PRoot-Distro is a wrapper utility for proot user-space emulator "
        "of chroot, bind --mount and binfmt_misc. This utility provides a "
        "convenient way for working with Linux containers, leveraging "
        "support of Docker registries to provide distributions of any kind.",
        width,
    )

    _section("COMMANDS")
    _commands_block(_TOP_COMMANDS, width)

    _section("GETTING HELP")
    _paragraph(
        f"Run '{PROGRAM_NAME} <command> --help' for details on any "
        "command.",
        width,
    )

    _section("QUICK START")
    _paragraph(
        "Usage of generic distribution images is straightforward. "
        "Below is an example for Ubuntu 24.04:",
        width,
    )
    msg()
    _shell_block(
        [f"{PROGRAM_NAME} install ubuntu:24.04",
         f"{PROGRAM_NAME} login ubuntu"], width,
    )
    msg()
    _paragraph(
        "Some images come preconfigured for specific purposes and "
        "contain built-in startup script. Often this is a case for "
        "server software:",
        width,
    )
    msg()
    _shell_block(
        [f"{PROGRAM_NAME} install nextcloud:32",
         f"{PROGRAM_NAME} run --redirect-ports nextcloud"], width,
    )
    msg()
    _paragraph(
        "Images that are not officially provided by Docker Hub "
        "require specifying organization or user name:",
        width,
    )
    msg()
    _shell_block(
        [f"{PROGRAM_NAME} install termux/termux-docker"], width,
    )
    msg()
    _paragraph(
        "If you no longer need a specific container, delete it with:",
        width,
    )
    msg()
    _shell_block(
        [f"{PROGRAM_NAME} remove ubuntu"], width,
    )
    msg()
    _paragraph(
        "You can discover existing images on Docker Hub "
        "(https://hub.docker.com/) or other places on the Internet. "
        "Current version of PRoot-Distro does not support building "
        "distribution images and you will need external utilities for "
        "that.",
        width,
    )

    _section("DATA LOCATION")
    msg(f"  {C['YELLOW']}{RUNTIME_DIR}{C['RST']}")

    _section("TROUBLESHOOTING")
    _paragraph(
        "If your terminal (theme) does not work well with colors, "
        "set this environment variable:",
        width
    )
    msg()
    _shell_block(
        ["export PD_FORCE_NO_COLORS=true"],
        width,
    )
    msg()
    _paragraph(
        "If you have issues with proot during login, try these "
        "quck troubleshooting steps:",
        width,
    )
    msg()
    _shell_block(
        ["pkg upgrade -y", f"PROOT_NO_SECCOMP=1 {PROGRAM_NAME} login <name>"],
        width,
    )
    msg()
    _paragraph(
        "Report utility issues to "
        "https://github.com/termux/proot-distro/issues",
        width,
    )

    _footer(width)
