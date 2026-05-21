# Fish completion for proot-distro and pd
#
# Install:
#   cp proot-distro.fish ~/.config/fish/completions/proot-distro.fish
#   cp proot-distro.fish ~/.config/fish/completions/pd.fish

# ---------------------------------------------------------------------------
# Helper: resolve installed containers directory
# ---------------------------------------------------------------------------
function __proot_distro_containers
    set -l dir
    if __proot_distro_is_termux
        set -l prefix
        if set -q TERMUX__PREFIX
            set prefix $TERMUX__PREFIX
        else
            set prefix /data/data/com.termux/files/usr
        end
        set dir "$prefix/var/lib/proot-distro/containers"
    else if set -q XDG_DATA_HOME
        set dir "$XDG_DATA_HOME/proot-distro/containers"
    else
        set dir "$HOME/.local/share/proot-distro/containers"
    end
    if test -d "$dir"
        for d in "$dir"/*/
            set -l name (basename "$d")
            if test -d "$dir/$name/rootfs"
                echo $name
            end
        end
    end
end

# ---------------------------------------------------------------------------
# Termux/Android detection — mirrors _detect_termux() in constants.py.
# Returns 0 (true) when at least two of three independent indicators match.
# ---------------------------------------------------------------------------
function __proot_distro_is_termux
    set -l score 0
    if test -f /system/build.prop; or test -d /data/app
        set score (math $score + 1)
    end
    if set -q TERMUX_APP__APP_VERSION_NAME; or set -q TERMUX_VERSION
        set score (math $score + 1)
    end
    set -l prefix
    if set -q TERMUX__PREFIX
        set prefix $TERMUX__PREFIX
    else
        set prefix /data/data/com.termux/files/usr
    end
    if test -r "$prefix" -a -x "$prefix"
        set score (math $score + 1)
    end
    test $score -ge 2
end

# ---------------------------------------------------------------------------
# Helper: true when no subcommand has been seen yet
# ---------------------------------------------------------------------------
function __proot_distro_no_subcommand
    not __fish_seen_subcommand_from \
        install remove rename reset login list backup restore \
        clear-cache copy sync run build push help
end

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n __proot_distro_no_subcommand -a install     -d 'Install a container from a Docker image or local archive'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a remove      -d 'Remove an installed container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a rename      -d 'Rename a container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a reset       -d 'Reinstall a container from its original image'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a login       -d 'Open a shell inside a container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a list        -d 'List installed containers'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a backup      -d 'Backup a container to a tar archive'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a restore     -d 'Restore a container from a tar archive'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a clear-cache -d 'Clear the download cache'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a copy        -d 'Copy files between host and container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a sync        -d 'Synchronize files between host and container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a run         -d 'Run the image entrypoint/cmd in a container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a build       -d 'Build an OCI image from a Dockerfile'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a push        -d 'Push a locally built image to a registry'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a help        -d 'Show help'

# Global help flag (before subcommand)
complete -c proot-distro -f -n __proot_distro_no_subcommand -s h -l help   -d 'Show help'

# ---------------------------------------------------------------------------
# install
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from install' \
    -s n -l name       -r -d 'Install under a custom container name'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install' \
    -s a -l architecture -r -d 'Target CPU architecture' \
    -a 'aarch64\tAArch64 arm\tARM(32-bit) i686\tx86(32-bit) riscv64\tRISC-V x86_64\tx86_64'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove' \
    -s v -l verbose    -d 'Print each removed file'
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from rename' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from rename' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from rename' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from reset' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from reset' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from reset' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# login
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -s u -l user       -r -d 'Run as this user (default: root)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -s P -l redirect-ports -d 'Redirect ports below 1024 to unprivileged range'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l isolated           -d 'Isolated mode: no host env vars or Termux paths'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l minimal            -d 'Like --isolated but also disables Android system bindings'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l shared-home        -d 'Mount home inside the container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l shared-tmp         -d 'Share /tmp with the host'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l shared-x11         -d 'Share the X11 socket (/tmp/.X11-unix)'
complete -c proot-distro -n '__fish_seen_subcommand_from login' \
    -s b -l bind       -r -d 'Bind-mount PATH[:DEST] into the container (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l no-link2symlink    -d 'Disable proot link2symlink extension'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l no-sysvipc         -d 'Disable SysV IPC emulation'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' \
    -l no-kill-on-exit    -d 'Do not kill child processes when the session ends'
complete -c proot-distro -n '__fish_seen_subcommand_from login' \
    -l emulator        -r -d 'Path to QEMU user-mode emulator binary'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l kernel          -r -d 'Fake kernel release string reported to uname'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l hostname        -r -d 'Hostname visible inside the container'
complete -c proot-distro -n '__fish_seen_subcommand_from login' \
    -s w -l work-dir   -r -d 'Initial working directory inside the container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -s e -l env        -r -d 'Set environment variable VAR=VALUE (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -l get-proot-cmd      -d 'Print the proot command line and exit'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login' \
    -s h -l help          -d 'Show help'

# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from list' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from list' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# backup
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -n '__fish_seen_subcommand_from backup' \
    -s o -l output     -r -d 'Write archive to FILE instead of stdout'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -s c -l compress   -r -d 'Compression algorithm' \
    -a 'gzip\tgzip bzip2\tbzip2 xz\txz none\tNo compression'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -s v -l verbose    -d 'Print each archived file'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------
complete -c proot-distro -n '__fish_seen_subcommand_from restore' \
    -s v -l verbose    -d 'Print each extracted file'
complete -c proot-distro -n '__fish_seen_subcommand_from restore' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -n '__fish_seen_subcommand_from restore' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# clear-cache
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from clear-cache' \
    -s v -l verbose    -d 'List removed files'
complete -c proot-distro -f -n '__fish_seen_subcommand_from clear-cache' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from clear-cache' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# copy
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -a '(__proot_distro_containers)' -d 'Container (use container:path notation)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s v -l verbose    -d 'Print each copied file'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s m -l move       -d 'Move instead of copy'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s r -l recursive  -d 'Copy directories recursively'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -a '(__proot_distro_containers)' -d 'Container (use container:path notation)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s v -l verbose    -d 'Print each synced file'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s c -l checksum      -d 'Use CRC32 checksum instead of size+mtime'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s d -l delete        -d 'Remove destination entries absent from source'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s h -l help          -d 'Show help'

# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s u -l user       -r -d 'Run as this user (default: root)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s P -l redirect-ports -d 'Redirect ports below 1024 to unprivileged range'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l isolated           -d 'Isolated mode: no host env vars or Termux paths'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l minimal            -d 'Like --isolated but also disables Android system bindings'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l shared-home        -d 'Mount home inside the container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l shared-tmp         -d 'Share /tmp with the host'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l shared-x11         -d 'Share the X11 socket (/tmp/.X11-unix)'
complete -c proot-distro -n '__fish_seen_subcommand_from run' \
    -s b -l bind       -r -d 'Bind-mount PATH[:DEST] into the container (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l no-link2symlink    -d 'Disable proot link2symlink extension'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l no-sysvipc         -d 'Disable SysV IPC emulation'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' \
    -l no-kill-on-exit    -d 'Do not kill child processes when the session ends'
complete -c proot-distro -n '__fish_seen_subcommand_from run' \
    -l emulator        -r -d 'Path to QEMU user-mode emulator binary'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l kernel          -r -d 'Fake kernel release string reported to uname'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l hostname        -r -d 'Hostname visible inside the container'
complete -c proot-distro -n '__fish_seen_subcommand_from run' \
    -s w -l work-dir   -r -d 'Initial working directory inside the container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s e -l env        -r -d 'Set environment variable VAR=VALUE (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l get-proot-cmd      -d 'Print the proot command line and exit'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s h -l help          -d 'Show help'

# ---------------------------------------------------------------------------
# build
# ---------------------------------------------------------------------------
complete -c proot-distro -n '__fish_seen_subcommand_from build' \
    -s f -l file       -r -d 'Path to Dockerfile (- reads from stdin)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s t -l tag        -r -d 'Image reference to assign (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -l build-arg       -r -d 'Set a build-time ARG (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s a -l architecture -r -d 'Target CPU architecture' \
    -a 'aarch64\tAArch64 arm\tARM(32-bit) i686\tx86(32-bit) riscv64\tRISC-V x86_64\tx86_64'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -l target          -r -d 'Stop after this named build stage'
complete -c proot-distro -n '__fish_seen_subcommand_from build' \
    -l emulator        -r -d 'Path to QEMU user-mode emulator binary'
complete -c proot-distro -n '__fish_seen_subcommand_from build' \
    -s o -l output     -r -d 'Write OCI tarball to FILE (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -l install-as      -r -d 'Install image as a local container after build' \
    -a '(__proot_distro_containers)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -l no-cache           -d 'Disable per-instruction build cache'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s v -l verbose       -d 'Echo each instruction and stream RUN output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s q -l quiet         -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from build' \
    -s h -l help          -d 'Show help'

# ---------------------------------------------------------------------------
# push
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from push' \
    -s a -l architecture -r -d 'Target CPU architecture' \
    -a 'aarch64\tAArch64 arm\tARM(32-bit) i686\tx86(32-bit) riscv64\tRISC-V x86_64\tx86_64'
complete -c proot-distro -f -n '__fish_seen_subcommand_from push' \
    -s q -l quiet      -d 'Suppress non-error output'
complete -c proot-distro -f -n '__fish_seen_subcommand_from push' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# help
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from help' \
    -a 'install remove rename reset login list backup restore clear-cache copy sync run build push' \
    -d 'Topic'

# ---------------------------------------------------------------------------
# pd (same entry point, duplicate all completions)
# ---------------------------------------------------------------------------
complete -c pd -f -n __proot_distro_no_subcommand -a install     -d 'Install a container from a Docker image or local archive'
complete -c pd -f -n __proot_distro_no_subcommand -a remove      -d 'Remove an installed container'
complete -c pd -f -n __proot_distro_no_subcommand -a rename      -d 'Rename a container'
complete -c pd -f -n __proot_distro_no_subcommand -a reset       -d 'Reinstall a container from its original image'
complete -c pd -f -n __proot_distro_no_subcommand -a login       -d 'Open a shell inside a container'
complete -c pd -f -n __proot_distro_no_subcommand -a list        -d 'List installed containers'
complete -c pd -f -n __proot_distro_no_subcommand -a backup      -d 'Backup a container to a tar archive'
complete -c pd -f -n __proot_distro_no_subcommand -a restore     -d 'Restore a container from a tar archive'
complete -c pd -f -n __proot_distro_no_subcommand -a clear-cache -d 'Clear the download cache'
complete -c pd -f -n __proot_distro_no_subcommand -a copy        -d 'Copy files between host and container'
complete -c pd -f -n __proot_distro_no_subcommand -a sync        -d 'Synchronize files between host and container'
complete -c pd -f -n __proot_distro_no_subcommand -a run         -d 'Run the image entrypoint/cmd in a container'
complete -c pd -f -n __proot_distro_no_subcommand -a build       -d 'Build an OCI image from a Dockerfile'
complete -c pd -f -n __proot_distro_no_subcommand -a push        -d 'Push a locally built image to a registry'
complete -c pd -f -n __proot_distro_no_subcommand -a help        -d 'Show help'
complete -c pd -f -n __proot_distro_no_subcommand -s h -l help   -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from install' -s n -l name         -r -d 'Install under a custom container name'
complete -c pd -f -n '__fish_seen_subcommand_from install' -s a -l architecture -r -d 'Target CPU architecture' -a 'aarch64 arm i686 riscv64 x86_64'
complete -c pd -f -n '__fish_seen_subcommand_from install' -s q -l quiet           -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from install' -s h -l help             -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from remove' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from remove' -s v -l verbose -d 'Print each removed file'
complete -c pd -f -n '__fish_seen_subcommand_from remove' -s q -l quiet   -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from remove' -s h -l help    -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from rename' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from rename' -s q -l quiet -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from rename' -s h -l help -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from reset' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from reset' -s q -l quiet -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from reset' -s h -l help -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from login' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from login' -s u -l user         -r -d 'Run as this user'
complete -c pd -f -n '__fish_seen_subcommand_from login' -s P -l redirect-ports   -d 'Redirect ports below 1024'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l isolated -d 'Isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l minimal  -d 'Minimal isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l shared-home          -d 'Mount home inside container'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l shared-tmp           -d 'Share /tmp with host'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l shared-x11           -d 'Share X11 socket'
complete -c pd -n   '__fish_seen_subcommand_from login' -s b -l bind          -r -d 'Bind-mount path (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l no-link2symlink -d 'Disable link2symlink'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l no-sysvipc     -d 'Disable SysV IPC'
complete -c pd -f -n '__fish_seen_subcommand_from login; and __proot_distro_is_termux' -l no-kill-on-exit -d 'Do not kill on exit'
complete -c pd -n   '__fish_seen_subcommand_from login' -l emulator            -r -d 'Emulator binary path'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l kernel             -r -d 'Fake kernel release'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l hostname           -r -d 'Container hostname'
complete -c pd -n   '__fish_seen_subcommand_from login' -s w -l work-dir       -r -d 'Working directory'
complete -c pd -f -n '__fish_seen_subcommand_from login' -s e -l env           -r -d 'Environment variable'
complete -c pd -f -n '__fish_seen_subcommand_from login' -l get-proot-cmd         -d 'Print proot command'
complete -c pd -f -n '__fish_seen_subcommand_from login' -s h -l help             -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from list' -s q -l quiet -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from list' -s h -l help  -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from backup' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -n   '__fish_seen_subcommand_from backup' -s o -l output    -r -d 'Output archive file'
complete -c pd -f -n '__fish_seen_subcommand_from backup' -s c -l compress -r -d 'Compression type' -a 'gzip bzip2 xz none'
complete -c pd -f -n '__fish_seen_subcommand_from backup' -s v -l verbose -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from backup' -s q -l quiet   -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from backup' -s h -l help    -d 'Show help'

complete -c pd -n '__fish_seen_subcommand_from restore' -s v -l verbose -d 'Verbose output'
complete -c pd -n '__fish_seen_subcommand_from restore' -s q -l quiet   -d 'Suppress non-error output'
complete -c pd -n '__fish_seen_subcommand_from restore' -s h -l help    -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from clear-cache' -s v -l verbose -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from clear-cache' -s q -l quiet   -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from clear-cache' -s h -l help    -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from copy' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s v -l verbose   -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s q -l quiet     -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s m -l move      -d 'Move instead of copy'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s r -l recursive -d 'Recursive copy'
complete -c pd -f -n '__fish_seen_subcommand_from copy' -s h -l help      -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from sync' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s v -l verbose      -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s q -l quiet        -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s c -l checksum     -d 'Use CRC32 checksum'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s d -l delete       -d 'Delete extra destination files'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s h -l help         -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from run' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s u -l user         -r -d 'Run as this user'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s P -l redirect-ports   -d 'Redirect ports below 1024'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l isolated -d 'Isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l minimal  -d 'Minimal isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l shared-home          -d 'Mount home inside container'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l shared-tmp           -d 'Share /tmp with host'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l shared-x11           -d 'Share X11 socket'
complete -c pd -n   '__fish_seen_subcommand_from run' -s b -l bind          -r -d 'Bind-mount path (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l no-link2symlink -d 'Disable link2symlink'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l no-sysvipc     -d 'Disable SysV IPC'
complete -c pd -f -n '__fish_seen_subcommand_from run; and __proot_distro_is_termux' -l no-kill-on-exit -d 'Do not kill on exit'
complete -c pd -n   '__fish_seen_subcommand_from run' -l emulator            -r -d 'Emulator binary path'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l kernel             -r -d 'Fake kernel release'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l hostname           -r -d 'Container hostname'
complete -c pd -n   '__fish_seen_subcommand_from run' -s w -l work-dir       -r -d 'Working directory'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s e -l env           -r -d 'Environment variable'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l get-proot-cmd         -d 'Print proot command'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s h -l help             -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from build' -s f -l file         -r -d 'Path to Dockerfile (- reads from stdin)'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s t -l tag          -r -d 'Image reference to assign (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from build' -l build-arg         -r -d 'Set a build-time ARG (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s a -l architecture -r -d 'Target CPU architecture' -a 'aarch64 arm i686 riscv64 x86_64'
complete -c pd -f -n '__fish_seen_subcommand_from build' -l target            -r -d 'Stop after this named build stage'
complete -c pd -n   '__fish_seen_subcommand_from build' -l emulator           -r -d 'Emulator binary path'
complete -c pd -n   '__fish_seen_subcommand_from build' -s o -l output        -r -d 'Write OCI tarball to FILE (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from build' -l install-as        -r -d 'Install image as a local container after build' -a '(__proot_distro_containers)'
complete -c pd -f -n '__fish_seen_subcommand_from build' -l no-cache             -d 'Disable per-instruction build cache'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s v -l verbose          -d 'Echo each instruction and stream RUN output'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s q -l quiet            -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from build' -s h -l help             -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from push' -s a -l architecture -r -d 'Target CPU architecture' -a 'aarch64 arm i686 riscv64 x86_64'
complete -c pd -f -n '__fish_seen_subcommand_from push' -s q -l quiet           -d 'Suppress non-error output'
complete -c pd -f -n '__fish_seen_subcommand_from push' -s h -l help            -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from help' \
    -a 'install remove rename reset login list backup restore clear-cache copy sync run build push' -d 'Topic'
