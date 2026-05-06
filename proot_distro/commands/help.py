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
from proot_distro.constants import PROGRAM_NAME, RUNTIME_DIR, TERMUX_APP_PACKAGE
from proot_distro.colors import C, msg, show_version


_HELP_COMMANDS = {
    "backup": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}backup {C['CYAN']}[OPTIONS] DISTRIBUTION{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Command aliases: {C['GREEN']}bak{C['CYAN']}, {C['GREEN']}bkp{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Back up a specified distribution installation into TAR archive.{C['RST']}\n"
          f"{C['CYAN']}Compression determined by output file extension or used options.{C['RST']}\n"
          f"{C['CYAN']}Data sent via stdout is not compressed by default.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Options:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--help{C['RST']}\n"
          "\n"
          f"    {C['CYAN']}Show this help information.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--compress [TYPE]{C['RST']}\n"
          "\n"
          f"    {C['CYAN']}Force a specific compression algorithm. Takes priority over{C['RST']}\n"
          f"    {C['CYAN']}the file extension. Supported values: gzip, bzip2, xz, none.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--output [file]{C['RST']}\n"
          "\n"
          f"    {C['CYAN']}Write contents to specified file instead of stdout. When no{C['RST']}\n"
          f"    {C['CYAN']}option '--compress' is given, compression is inferred from{C['RST']}\n"
          f"    {C['CYAN']}the file extension (like tar, tar.gz or txz).{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Selected distribution should be referenced by alias which can be{C['RST']}\n"
          f"{C['CYAN']}obtained by this command: {C['GREEN']}{PROGRAM_NAME} list{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
    "clear-cache": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}clear-cache{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Command aliases: {C['GREEN']}clear, cl{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Remove all cached rootfs archives.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
    "copy": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}copy {C['CYAN']}[OPTIONS] [DIST:]SRC [DIST:]DEST{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Command aliases: {C['GREEN']}cp{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Copy files from/to distribution.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Both source and destination arguments may be either as a local{C['RST']}\n"
          f"{C['CYAN']}path or path of file inside distribution container.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Options:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--help             {C['CYAN']}- Show this help information.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--move             {C['CYAN']}- Move instead of copying.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--verbose          {C['CYAN']}- Show the log of copied files.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Glob is not supported. Only one file or directory can be copied{C['RST']}\n"
          f"{C['CYAN']}at a time.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Example how to copy local file to distribution:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}{PROGRAM_NAME} copy ./file.txt ubuntu:/root/file.txt{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
    "install": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}install {C['CYAN']}[OPTIONS] IMAGE{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Command aliases: {C['GREEN']}add{C['CYAN']}, {C['GREEN']}i{C['CYAN']}, {C['GREEN']}in{C['CYAN']}, {C['GREEN']}ins{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Pull a Docker Hub image and install it as a proot container.{C['RST']}\n"
          f"{C['CYAN']}IMAGE is a Docker image reference such as 'ubuntu:24.04' or{C['RST']}\n"
          f"{C['CYAN']}'alpine:3.21'. Official images can be referenced by name alone{C['RST']}\n"
          f"{C['CYAN']}('ubuntu'), user images require the 'user/image' form. The tag{C['RST']}\n"
          f"{C['CYAN']}defaults to 'latest' when omitted.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Layers are cached in the download cache directory and reused on{C['RST']}\n"
          f"{C['CYAN']}subsequent installs of the same image.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Options:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--help               {C['CYAN']}- Show this help information.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--name [alias]       {C['CYAN']}- Set a custom local alias for the installed{C['RST']}\n"
          f"                         {C['CYAN']}distribution (default: image name).{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--architecture [arch]{C['CYAN']}- Override the target CPU architecture.{C['RST']}\n"
          f"                         {C['CYAN']}Valid values: aarch64, arm, i686,{C['RST']}\n"
          f"                         {C['CYAN']}riscv64, x86_64.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Examples:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}{PROGRAM_NAME} install ubuntu:24.04{C['RST']}\n"
          f"  {C['GREEN']}{PROGRAM_NAME} install alpine:3.21 --name my-alpine{C['RST']}\n"
          f"  {C['GREEN']}{PROGRAM_NAME} install debian:bookworm --architecture aarch64{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
    "list": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}list {C['CYAN']}[OPTIONS]{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Command aliases: {C['GREEN']}li{C['CYAN']}, {C['GREEN']}ls{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Options:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--help             {C['CYAN']}- Show this help information.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--detailed         {C['CYAN']}- Show version, status and other info.{C['RST']}\n"
          "\n"
        ),
        show_version(),
        msg(),
    ),
    "login": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}login {C['CYAN']}[OPTIONS] DISTRIBUTION [-- COMMAND]{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Command aliases: {C['GREEN']}sh{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Spawn a shell for specified distribution or execute custom command.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Options:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--help             {C['CYAN']}- Show this help information.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--user [user]      {C['CYAN']}- Login as specified user instead of 'root'.{C['RST']}\n"
          f"                       {C['CYAN']}Not applicable for distribution 'termux'.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--redirect-ports   {C['CYAN']}- Transparently redirect privileged ports 1-1023{C['RST']}\n"
          f"                       {C['CYAN']}to a higher port number.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--isolated         {C['CYAN']}- Run isolated environment without access{C['RST']}\n"
          f"                       {C['CYAN']}to host file system.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--termux-home      {C['CYAN']}- Mount Termux home directory to /root.{C['RST']}\n"
          f"                       {C['CYAN']}Takes priority over '{C['GREEN']}--isolated{C['CYAN']}' option.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--shared-tmp       {C['CYAN']}- Mount Termux temp directory to /tmp.{C['RST']}\n"
          f"                       {C['CYAN']}Takes priority over '{C['GREEN']}--isolated{C['CYAN']}' option.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--bind [src:dest]  {C['CYAN']}- Custom file system binding. Can be specified{C['RST']}\n"
          f"                       {C['CYAN']}multiple times.{C['RST']}\n"
          f"                       {C['CYAN']}Takes priority over '{C['GREEN']}--isolated{C['CYAN']}' option.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--no-link2symlink  {C['CYAN']}- Disable hardlink emulation by proot.{C['RST']}\n"
          f"                       {C['CYAN']}Adviseable only on devices with SELinux{C['RST']}\n"
          f"                       {C['CYAN']}in permissive mode. Not applicable for{C['RST']}\n"
          f"                       {C['CYAN']}distribution 'termux'.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--no-sysvipc       {C['CYAN']}- Disable System V IPC emulation by proot.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--no-kill-on-exit  {C['CYAN']}- Wait until all running processes will finish{C['RST']}\n"
          f"                       {C['CYAN']}before exiting. This will cause proot to{C['RST']}\n"
          f"                       {C['CYAN']}freeze if you are running daemons.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--no-arch-warning  {C['CYAN']}- Suppress warning about CPU not supporting 32-bit{C['RST']}\n"
          f"                       {C['CYAN']}instructions.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--emulator [path]  {C['CYAN']}- Override the emulator binary used for cross-arch{C['RST']}\n"
          f"                       {C['CYAN']}execution (e.g. /path/to/qemu-x86_64). The file{C['RST']}\n"
          f"                       {C['CYAN']}must exist and be executable.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--kernel [text]    {C['CYAN']}- Customize Linux kernel release string shown by{C['RST']}\n"
          f"                       {C['CYAN']}uname command.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--hostname [text]  {C['CYAN']}- Customize system host name.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--work-dir [path]  {C['CYAN']}- Set the working directory.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--env ENV=val      {C['CYAN']}- Set environment variable. Can be specified{C['RST']}\n"
          f"                       {C['CYAN']}multiple times.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Put '{C['GREEN']}--{C['CYAN']}' if you wish to stop command line processing and pass{C['RST']}\n"
          f"{C['CYAN']}options as shell arguments.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}If no '{C['GREEN']}--isolated{C['CYAN']}' option given, the following host directories{C['RST']}\n"
          f"{C['CYAN']}will be available:{C['RST']}\n"
          "\n"
          f"  {C['CYAN']}* {C['YELLOW']}/apex {C['CYAN']}(only Android 10+){C['RST']}\n"
          f"  {C['CYAN']}* {C['YELLOW']}/data/dalvik-cache{C['RST']}\n"
          f"  {C['CYAN']}* {C['YELLOW']}/data/data/{TERMUX_APP_PACKAGE}{C['RST']}\n"
          f"  {C['CYAN']}* {C['YELLOW']}/sdcard{C['RST']}\n"
          f"  {C['CYAN']}* {C['YELLOW']}/storage{C['RST']}\n"
          f"  {C['CYAN']}* {C['YELLOW']}/system{C['RST']}\n"
          f"  {C['CYAN']}* {C['YELLOW']}/vendor{C['RST']}\n"
          "\n"
          f"{C['CYAN']}This should be enough to get Termux utilities like termux-api or{C['RST']}\n"
          f"{C['CYAN']}termux-open get working. If they do not work for some reason,{C['RST']}\n"
          f"{C['CYAN']}make sure they are properly set in {C['YELLOW']}/etc/environment{C['CYAN']}.{C['RST']}\n"
          f"{C['CYAN']}Also check whether they define variables like ANDROID_DATA,{C['RST']}\n"
          f"{C['CYAN']}ANDROID_ROOT, BOOTCLASSPATH and others which are usually set{C['RST']}\n"
          f"{C['CYAN']}in Termux sessions.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}If issue occurs only after su/sudo use, then likely your PAM{C['RST']}\n"
          f"{C['CYAN']}configuration doesn't load {C['YELLOW']}/etc/environment{C['CYAN']} and you need to fix{C['RST']}\n"
          f"{C['CYAN']}it by enabling pam_env.so in /etc/pam.d configuration.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Example PAM configuration line:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}session  required  pam_env.so readenv=1{C['RST']}\n"
          "\n"
          f"{C['CYAN']}You need to append it to {C['YELLOW']}/etc/pam.d/su{C['CYAN']}, {C['YELLOW']}/etc/pam.d/sudo{C['CYAN']} or other{C['RST']}\n"
          f"{C['CYAN']}file depending on distribution.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}When using distribution 'termux' some of mentioned directories{C['RST']}\n"
          f"{C['CYAN']}such as /system are always mounted to satisfy requirements.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
    "remove": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}remove {C['CYAN']}DISTRIBUTION{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Command aliases: {C['GREEN']}rm{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Remove a specified Linux distribution.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Options:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--help             {C['CYAN']}- Show this help information.{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--verbose          {C['CYAN']}- Show the log of deleted files.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Be careful when using it because you will not be prompted for{C['RST']}\n"
          f"{C['CYAN']}confirmation and all data saved within the distribution will{C['RST']}\n"
          f"{C['CYAN']}instantly gone.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
    "rename": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}rename {C['CYAN']}OLDNAME NEWNAME{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Command aliases: {C['GREEN']}mv{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Rename an installed distribution.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Options:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--help             {C['CYAN']}- Show this help information.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Note that renaming default distribution will take a while{C['RST']}\n"
          f"{C['CYAN']}as PRoot-Distro has to update symlinks. If user renames a{C['RST']}\n"
          f"{C['CYAN']}default distribution, the plug-in copy will be created.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
    "reset": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}reset {C['CYAN']}DISTRIBUTION{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Reinstall the specified Linux distribution from scratch.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Options:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--help             {C['CYAN']}- Show this help information.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Be careful when using it because you will not be prompted for{C['RST']}\n"
          f"{C['CYAN']}confirmation and all data saved within the distribution will{C['RST']}\n"
          f"{C['CYAN']}instantly gone.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
    "restore": lambda: (
        msg(
          "\n"
          f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME} {C['GREEN']}restore {C['CYAN']}[OPTIONS] [BACKUP_FILE]{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Restore a distribution from archive. When file is{C['RST']}\n"
          f"{C['CYAN']}not specified, the archive data expected from stdin.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Options:{C['RST']}\n"
          "\n"
          f"  {C['GREEN']}--help             {C['CYAN']}- Show this help information.{C['RST']}\n"
          "\n"
          f"{C['CYAN']}Compression is detected automatically from the file header{C['RST']}\n"
          f"{C['CYAN']}Supported formats: gzip, bzip2, xz and uncompressed tar.{C['RST']}\n"
          f"{C['CYAN']}This applies to both file and stdin input.{C['RST']}\n"
        ),
        show_version(),
        msg(),
    ),
}


def command_help() -> None:
    msg(
      "\n"
      f"{C['BYELLOW']}Usage: {C['BCYAN']}{PROGRAM_NAME}{C['CYAN']} [{C['GREEN']}COMMAND{C['CYAN']}] [ARGUMENTS]{C['RST']}\n"
      "\n"
      f"{C['CYAN']}PRoot-Distro is an utility that makes it simple to install,{C['RST']}\n"
      f"{C['CYAN']}manage and run rootless containers in Termux. It creates{C['RST']}\n"
      f"{C['CYAN']}environments mimicking a standard Linux chroot among with{C['RST']}\n"
      f"{C['CYAN']}support of directory bindings and foreign CPU emulation.{C['RST']}\n"
      f"{C['CYAN']}Everything because of proot used as container engine.{C['RST']}\n"
      "\n"
      f"{C['CYAN']}List of the available commands:{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}help         {C['CYAN']}- Show this help information.{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}backup       {C['CYAN']}- Backup a specified distribution.{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}install      {C['CYAN']}- Install a specified distribution.{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}list         {C['CYAN']}- List supported distributions and their{C['RST']}\n"
      f"                 {C['CYAN']}installation status.{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}login        {C['CYAN']}- Start login shell for the specified distribution.{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}remove       {C['CYAN']}- Delete a specified distribution.{C['RST']}\n"
      f"                 {C['RED']}WARNING: this command destroys data!{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}rename       {C['CYAN']}- Rename installed distribution.{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}reset        {C['CYAN']}- Reinstall from scratch a specified distribution.{C['RST']}\n"
      f"                 {C['RED']}WARNING: this command destroys data!{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}restore      {C['CYAN']}- Restore a specified distribution.{C['RST']}\n"
      f"                 {C['RED']}WARNING: this command destroys data!{C['RST']}\n"
      "\n"
      f"  {C['GREEN']}clear-cache  {C['CYAN']}- Clear cache of downloaded files. {C['RST']}\n"
      "\n"
      f"  {C['GREEN']}copy         {C['CYAN']}- Copy files from/to distribution. {C['RST']}\n"
      "\n"
      f"{C['CYAN']}Each of commands has its own help information. To view it, just{C['RST']}\n"
      f"{C['CYAN']}supply a '{C['GREEN']}--help{C['CYAN']}' argument to chosen command.{C['RST']}\n"
      "\n"
      f"{C['CYAN']}Hint: install command pulls directly from Docker Hub. Specify any{C['RST']}\n"
      f"{C['CYAN']}public image reference, e.g.:{C['RST']}\n"
      f"  {C['GREEN']}{PROGRAM_NAME} install ubuntu:24.04{C['RST']}\n"
      f"  {C['GREEN']}{PROGRAM_NAME} install alpine:3.21{C['RST']}\n"
      "\n"
      f"{C['CYAN']}Runtime data is stored at this location:{C['RST']}\n"
      "\n"
      f"  {C['YELLOW']}{RUNTIME_DIR}{C['RST']}\n"
      "\n"
      f"{C['CYAN']}If you have issues with proot during installation or login, try{C['RST']}\n"
      f"{C['CYAN']}to set '{C['GREEN']}PROOT_NO_SECCOMP=1{C['CYAN']}' environment variable.{C['RST']}\n"
    )
    show_version()
    msg()
