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

# Architecture: All user-facing help text lives here so it can be updated
# independently of command logic. Each entry in _HELP_COMMANDS is a lambda
# that calls msg() with pre-formatted text. Lines are kept within 66 display
# columns (per project spec) so the output looks good on a narrow terminal.

from proot_distro.constants import (
    PROGRAM_NAME,
    RUNTIME_DIR,
    TERMUX_APP_PACKAGE,
)
from proot_distro.colors import C, msg, show_version

# ---------------------------------------------------------------------------
# Per-command help lambdas
# ---------------------------------------------------------------------------

_HELP_COMMANDS = {
    "backup": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}backup"
            f" {C['CYAN']}[OPTIONS] CONTAINER{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Command aliases: {C['GREEN']}bak"
            f"{C['CYAN']}, {C['GREEN']}bkp{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Back up a specified container into a TAR archive.\n"
            f"Compression is determined by the output file extension\n"
            f"or by the --compress option. Output to stdout is\n"
            f"uncompressed by default.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--compress [TYPE]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Force a specific compression algorithm,\n"
            f"    overriding the file extension. Supported values:\n"
            f"    gzip, bzip2, xz, none.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--output [FILE]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Write the archive to FILE instead of stdout.\n"
            f"    When --compress is not given, compression is\n"
            f"    inferred from the file extension (.tar.gz, .txz,\n"
            f"    etc.).{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--verbose{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Log each file name as it is added to the\n"
            f"    archive.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Example:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}{PROGRAM_NAME} backup ubuntu"
            f" --output ubuntu.tar.xz{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "clear-cache": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}clear-cache{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Command aliases: {C['GREEN']}clear"
            f"{C['CYAN']}, {C['GREEN']}cl{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Remove all cached Docker image layers and\n"
            f"manifests from the download cache directory.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--verbose{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Log each removed file.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "copy": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}copy"
            f" {C['CYAN']}[OPTIONS] [DIST:]SRC [DIST:]DEST{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Command aliases: {C['GREEN']}cp{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Copy files between the host filesystem and a\n"
            f"proot container. Both source and destination may be\n"
            f"a local path or a 'container:path' reference.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--move{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Move instead of copying (delete the source\n"
            f"    after a successful copy).{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--recursive{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Copy directories recursively, preserving\n"
            f"    symlinks (equivalent to cp -a).{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--verbose{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Log each copied file.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Directories '.' or '..' are only accepted as\n"
            f"source, not as destination. Glob patterns are not\n"
            f"supported.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Example — copy local file into container:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}{PROGRAM_NAME} copy ./file.txt"
            f" ubuntu:/root/file.txt{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "install": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}install"
            f" {C['CYAN']}[OPTIONS] IMAGE{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Command aliases: {C['GREEN']}add"
            f"{C['CYAN']}, {C['GREEN']}i"
            f"{C['CYAN']}, {C['GREEN']}in"
            f"{C['CYAN']}, {C['GREEN']}ins{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Pull a Docker/OCI image and install it as a proot\n"
            f"container. IMAGE is a Docker image reference such as\n"
            f"'ubuntu:24.04' or 'alpine:3.21'. Official images can\n"
            f"be referenced by name alone ('ubuntu'); user images\n"
            f"require the 'user/image' form. A custom registry host\n"
            f"can be prefixed: 'ghcr.io/foo/bar:tag'. The tag\n"
            f"defaults to 'latest' when omitted.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Layers are cached locally and reused on subsequent\n"
            f"installs of the same image.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--name [ALIAS]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Set a custom local name for the container\n"
            f"    (default: image name without tag).{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--architecture [ARCH]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Override the target CPU architecture.\n"
            f"    Valid values: aarch64, arm, i686, riscv64,\n"
            f"    x86_64.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Examples:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}{PROGRAM_NAME} install ubuntu:24.04{C['RST']}\n"
            f"  {C['GREEN']}{PROGRAM_NAME} install alpine:3.21"
            f" --name my-alpine{C['RST']}\n"
            f"  {C['GREEN']}{PROGRAM_NAME} install debian:bookworm"
            f" --architecture aarch64{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "list": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}list{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Command aliases: {C['GREEN']}li"
            f"{C['CYAN']}, {C['GREEN']}ls{C['RST']}\n"
            "\n"
            f"{C['CYAN']}List all installed proot containers.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "login": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}login"
            f" {C['CYAN']}[OPTIONS] CONTAINER [-- COMMAND]{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Command aliases: {C['GREEN']}sh{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Spawn a shell inside the container, or run a\n"
            f"custom COMMAND when given after '--'.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--user [USER]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Login as USER instead of root.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--redirect-ports{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Transparently redirect privileged ports\n"
            f"    1-1023 to higher port numbers.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--isolated{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Run without host filesystem access\n"
            f"    (except mandatory bindings).{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--minimal{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Launch bare-minimum proot configuration.\n"
            f"    Only /dev, /proc and /sys are bound. No Android\n"
            f"    dirs, no fake sysdata, no kernel release override.\n"
            f"    Only --env, TERM, COLORTERM and PROOT_L2S_DIR\n"
            f"    are exported.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--termux-home{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Bind Termux home directory into the container.\n"
            f"    Takes priority over --isolated.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--shared-tmp{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Bind Termux tmp directory to /tmp.\n"
            f"    Takes priority over --isolated.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--shared-x11{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Bind Termux X11 socket directory to\n"
            f"    /tmp/.X11-unix. Takes priority over\n"
            f"    --isolated.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--bind [SRC:DEST]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Custom filesystem binding. Can be specified\n"
            f"    multiple times. Takes priority over --isolated.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--no-link2symlink{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Disable hardlink emulation by proot.\n"
            f"    Advisable only with SELinux in permissive mode.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--no-sysvipc{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Disable System V IPC emulation by proot.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--no-kill-on-exit{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Wait for all processes to finish before\n"
            f"    exiting (causes proot to freeze if daemons\n"
            f"    are running).{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--no-arch-warning{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Suppress the 32-bit CPU support warning.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--emulator [PATH]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Override the QEMU emulator binary for\n"
            f"    cross-arch execution. FILE must be executable.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--kernel [TEXT]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Customize the kernel release string reported\n"
            f"    by uname.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--hostname [TEXT]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Customize the system hostname.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--work-dir [PATH]{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Set the initial working directory.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--env VAR=VALUE{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Set an environment variable. Can be specified\n"
            f"    multiple times.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--debug{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Print the fully assembled proot command line\n"
            f"    and exit without running it. The output is ready\n"
            f"    to copy and paste into a terminal.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Without --isolated, the following host paths are\n"
            f"available inside the container:{C['RST']}\n"
            "\n"
            f"  {C['CYAN']}* {C['YELLOW']}/apex"
            f" {C['CYAN']}(Android 10+ only){C['RST']}\n"
            f"  {C['CYAN']}* {C['YELLOW']}/data/dalvik-cache{C['RST']}\n"
            f"  {C['CYAN']}* {C['YELLOW']}/data/data/{TERMUX_APP_PACKAGE}{C['RST']}\n"
            f"  {C['CYAN']}* {C['YELLOW']}/sdcard{C['RST']}\n"
            f"  {C['CYAN']}* {C['YELLOW']}/storage{C['RST']}\n"
            f"  {C['CYAN']}* {C['YELLOW']}/system{C['RST']}\n"
            f"  {C['CYAN']}* {C['YELLOW']}/vendor{C['RST']}\n"
            "\n"
            f"{C['CYAN']}If Termux utilities like termux-api do not work,\n"
            f"ensure /etc/environment defines ANDROID_DATA, ANDROID_ROOT,\n"
            f"BOOTCLASSPATH, and related variables.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "remove": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}remove"
            f" {C['CYAN']}[OPTIONS] CONTAINER{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Command aliases: {C['GREEN']}rm{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Permanently delete the specified proot container\n"
            f"and all its data. No confirmation is requested.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--verbose{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Log each deleted file.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "rename": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}rename"
            f" {C['CYAN']}OLDNAME NEWNAME{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Rename an installed proot container.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Renaming updates all proot link2symlink entries\n"
            f"inside the container, which may take a while for large\n"
            f"rootfs trees.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "reset": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}reset"
            f" {C['CYAN']}CONTAINER{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Rebuild the specified container from scratch\n"
            f"using the stored Docker image manifest. All current\n"
            f"data inside the container will be lost.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "run": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}run"
            f" {C['CYAN']}[OPTIONS] CONTAINER [-- ARG ...]{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Run the Entrypoint and/or Cmd defined in the\n"
            f"container's Docker image manifest. Arguments given\n"
            f"after '--' are appended to Entrypoint (replacing the\n"
            f"image-defined Cmd). If neither Entrypoint nor Cmd is\n"
            f"defined and no arguments are given, an error is\n"
            f"reported.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Accepts the same options as the login command,\n"
            f"including --debug to print the assembled proot\n"
            f"command line without executing it.\n"
            f"See: {C['GREEN']}{PROGRAM_NAME} login --help{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Examples:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}{PROGRAM_NAME} run ubuntu{C['RST']}\n"
            f"  {C['GREEN']}{PROGRAM_NAME} run ubuntu -- --version{C['RST']}\n"
            f"  {C['GREEN']}{PROGRAM_NAME} run ubuntu"
            f" --isolated -- /bin/echo hi{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "sync": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}sync"
            f" {C['CYAN']}[OPTIONS] [DIST:]SRC [DIST:]DEST{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Synchronize SRC to DEST, copying only files that\n"
            f"differ. Both paths may be plain host paths or container\n"
            f"references in 'container:path' form. Always recursive;\n"
            f"no --recursive flag is needed.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Comparison: size + modification time by default;\n"
            f"size + CRC32 checksum with --checksum.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Symlinks are copied as-is. Hard links become\n"
            f"independent copies. Block/char devices, FIFOs, and\n"
            f"sockets are silently skipped. File ownership is never\n"
            f"changed. Access modes and timestamps are preserved.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--checksum{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Compare files by size and CRC32 checksum\n"
            f"    instead of size and modification time.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--delete{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}After syncing, remove destination files and\n"
            f"    directories that have no counterpart in the source.\n"
            f"    Only effective when source is a directory.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--verbose{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Log each synced or deleted entry.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Examples:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}{PROGRAM_NAME} sync ./app ubuntu:/opt/app{C['RST']}\n"
            f"  {C['GREEN']}{PROGRAM_NAME} sync ubuntu:/etc ./backup/etc"
            f"{C['RST']}\n"
            f"  {C['GREEN']}{PROGRAM_NAME} sync --checksum"
            f" ./data ubuntu:/data{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),

    "restore": lambda: (
        msg(
            "\n"
            f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
            f" {C['GREEN']}restore"
            f" {C['CYAN']}[OPTIONS] [BACKUP_FILE]{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Restore a container from a backup archive.\n"
            f"When FILE is not specified, archive data is read\n"
            f"from stdin.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Options:{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--help{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Show this help information.{C['RST']}\n"
            "\n"
            f"  {C['GREEN']}--verbose{C['RST']}\n"
            "\n"
            f"    {C['CYAN']}Log each extracted file.{C['RST']}\n"
            "\n"
            f"{C['CYAN']}Compression is detected automatically from the\n"
            f"file header. Supported: gzip, bzip2, xz, uncompressed\n"
            f"tar. Applies to both file and stdin input.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
}


# ---------------------------------------------------------------------------
# Top-level help
# ---------------------------------------------------------------------------

def command_help(args=None, configs=None) -> None:  # noqa: ARG001
    msg(
        "\n"
        f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}"
        f"{C['CYAN']} [{C['GREEN']}COMMAND{C['CYAN']}] [ARGUMENTS]{C['RST']}\n"
        "\n"
        f"{C['CYAN']}PRoot-Distro is a utility that makes it simple to\n"
        f"install, manage and run rootless Linux containers in\n"
        f"Termux. It creates environments mimicking a standard\n"
        f"Linux chroot, with support for directory bindings and\n"
        f"foreign CPU emulation via proot.{C['RST']}\n"
        "\n"
        f"{C['CYAN']}Available commands:{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}help        {C['CYAN']}- Show this help.{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}install     {C['CYAN']}- Install a container from a Docker\n"
        f"               image.{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}list        {C['CYAN']}- List installed containers.{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}login       {C['CYAN']}- Start a shell inside a container.{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}remove      {C['CYAN']}- Delete a container."
        f" {C['RED']}Destroys data!{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}rename      {C['CYAN']}- Rename a container.{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}reset       {C['CYAN']}- Reinstall a container from scratch."
        f" {C['RED']}Destroys data!{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}backup      {C['CYAN']}- Archive a container to a TAR file.{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}restore     {C['CYAN']}- Restore a container from a TAR file."
        f" {C['RED']}Destroys data!{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}clear-cache {C['CYAN']}- Delete cached Docker layers.{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}copy        {C['CYAN']}- Copy files from/to container.{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}sync        {C['CYAN']}- Sync files from/to container.{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}run         {C['CYAN']}- Run container image entrypoint.{C['RST']}\n"
        "\n"
        f"{C['CYAN']}Each command has its own help page:\n"
        f"  {C['GREEN']}{PROGRAM_NAME} <command> --help{C['RST']}\n"
        "\n"
        f"{C['CYAN']}Quick start:{C['RST']}\n"
        "\n"
        f"  {C['GREEN']}{PROGRAM_NAME} install ubuntu:24.04{C['RST']}\n"
        f"  {C['GREEN']}{PROGRAM_NAME} login ubuntu{C['RST']}\n"
        "\n"
        f"{C['CYAN']}Runtime data is stored at:{C['RST']}\n"
        "\n"
        f"  {C['YELLOW']}{RUNTIME_DIR}{C['RST']}\n"
        "\n"
        f"{C['CYAN']}If you have issues with proot during login, try:\n"
        f"  {C['GREEN']}PROOT_NO_SECCOMP=1 {PROGRAM_NAME} login <name>{C['RST']}\n"
    )
    show_version()
    msg()
