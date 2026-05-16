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
    if set -q TERMUX_PREFIX
        set dir "$TERMUX_PREFIX/var/lib/proot-distro/containers"
    else if set -q ANDROID_ROOT
        set dir "/data/data/com.termux/files/usr/var/lib/proot-distro/containers"
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
# Helper: true when no subcommand has been seen yet
# ---------------------------------------------------------------------------
function __proot_distro_no_subcommand
    not __fish_seen_subcommand_from \
        install add i in ins \
        remove rm \
        rename \
        reset \
        login sh \
        list li ls \
        backup bak bkp \
        restore \
        clear-cache clear cl \
        copy cp \
        sync \
        run \
        help h he hel
end

# ---------------------------------------------------------------------------
# Subcommands
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n __proot_distro_no_subcommand -a install     -d 'Install a container from a Docker image or local archive'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a add         -d 'Alias for install'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a i           -d 'Alias for install'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a in          -d 'Alias for install'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a ins         -d 'Alias for install'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a remove      -d 'Remove an installed container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a rm          -d 'Alias for remove'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a rename      -d 'Rename a container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a reset       -d 'Reinstall a container from its original image'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a login       -d 'Open a shell inside a container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a sh          -d 'Alias for login'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a list        -d 'List installed containers'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a li          -d 'Alias for list'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a ls          -d 'Alias for list'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a backup      -d 'Backup a container to a tar archive'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a bak         -d 'Alias for backup'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a bkp         -d 'Alias for backup'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a restore     -d 'Restore a container from a tar archive'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a clear-cache -d 'Clear the download cache'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a clear       -d 'Alias for clear-cache'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a cl          -d 'Alias for clear-cache'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a copy        -d 'Copy files between host and container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a cp          -d 'Alias for copy'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a sync        -d 'Synchronize files between host and container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a run         -d 'Run the image entrypoint/cmd in a container'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a help        -d 'Show help'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a h           -d 'Alias for help'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a he          -d 'Alias for help'
complete -c proot-distro -f -n __proot_distro_no_subcommand -a hel         -d 'Alias for help'

# Global help flag (before subcommand)
complete -c proot-distro -f -n __proot_distro_no_subcommand -s h -l help   -d 'Show help'

# ---------------------------------------------------------------------------
# install / add / i / in / ins
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from install add i in ins' \
    -l name            -r -d 'Install under a custom container name'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install add i in ins' \
    -l override-alias  -r -d 'Install under a custom container name (alias for --name)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install add i in ins' \
    -l architecture    -r -d 'Target CPU architecture' \
    -a 'aarch64\tAArch64 arm\tARM arm\tARM(32-bit) i686\tx86(32-bit) riscv64\tRISC-V x86_64\tx86_64'
complete -c proot-distro -f -n '__fish_seen_subcommand_from install add i in ins' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# remove / rm
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove rm' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove rm' \
    -s v -l verbose    -d 'Print each removed file'
complete -c proot-distro -f -n '__fish_seen_subcommand_from remove rm' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# rename
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from rename' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from rename' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from reset' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from reset' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# login / sh
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l user            -r -d 'Run as this user (default: root)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l redirect-ports     -d 'Redirect ports below 1024 to unprivileged range'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l fix-low-ports      -d 'Alias for --redirect-ports'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l isolated           -d 'Isolated mode: no host env vars or Termux paths'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l minimal            -d 'Like --isolated but also disables Android system bindings'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l shared-home        -d 'Mount Termux home inside the container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l termux-home        -d 'Alias for --shared-home'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l shared-tmp         -d 'Share /tmp with the host'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l shared-x11         -d 'Share the X11 socket (/tmp/.X11-unix)'
complete -c proot-distro -n '__fish_seen_subcommand_from login sh' \
    -l bind            -r -d 'Bind-mount PATH[:DEST] into the container (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l no-link2symlink    -d 'Disable proot link2symlink extension'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l no-sysvipc         -d 'Disable SysV IPC emulation'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l no-kill-on-exit    -d 'Do not kill child processes when the session ends'
complete -c proot-distro -n '__fish_seen_subcommand_from login sh' \
    -l emulator        -r -d 'Path to QEMU user-mode emulator binary'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l kernel          -r -d 'Fake kernel release string reported to uname'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l hostname        -r -d 'Hostname visible inside the container'
complete -c proot-distro -n '__fish_seen_subcommand_from login sh' \
    -l work-dir        -r -d 'Initial working directory inside the container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l env             -r -d 'Set environment variable VAR=VALUE (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -l get-proot-cmd      -d 'Print the proot command line and exit'
complete -c proot-distro -f -n '__fish_seen_subcommand_from login sh' \
    -s h -l help          -d 'Show help'

# ---------------------------------------------------------------------------
# list / li / ls
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from list li ls' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# backup / bak / bkp
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup bak bkp' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -n '__fish_seen_subcommand_from backup bak bkp' \
    -l output          -r -d 'Write archive to FILE instead of stdout'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup bak bkp' \
    -l compress        -r -d 'Compression algorithm' \
    -a 'gzip\tgzip bzip2\tbzip2 xz\txz none\tNo compression'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup bak bkp' \
    -s v -l verbose    -d 'Print each archived file'
complete -c proot-distro -f -n '__fish_seen_subcommand_from backup bak bkp' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------
complete -c proot-distro -n '__fish_seen_subcommand_from restore' \
    -s v -l verbose    -d 'Print each extracted file'
complete -c proot-distro -n '__fish_seen_subcommand_from restore' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# clear-cache / clear / cl
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from clear-cache clear cl' \
    -s v -l verbose    -d 'List removed files'
complete -c proot-distro -f -n '__fish_seen_subcommand_from clear-cache clear cl' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# copy / cp
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy cp' \
    -a '(__proot_distro_containers)' -d 'Container (use container:path notation)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy cp' \
    -s v -l verbose    -d 'Print each copied file'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy cp' \
    -s m -l move       -d 'Move instead of copy'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy cp' \
    -s r -l recursive  -d 'Copy directories recursively'
complete -c proot-distro -f -n '__fish_seen_subcommand_from copy cp' \
    -s h -l help       -d 'Show help'

# ---------------------------------------------------------------------------
# sync
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -a '(__proot_distro_containers)' -d 'Container (use container:path notation)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s v -l verbose    -d 'Print each synced file'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -l checksum           -d 'Use CRC32 checksum instead of size+mtime'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -l delete             -d 'Remove destination entries absent from source'
complete -c proot-distro -f -n '__fish_seen_subcommand_from sync' \
    -s h -l help          -d 'Show help'

# ---------------------------------------------------------------------------
# run
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -a '(__proot_distro_containers)' -d 'Container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l user            -r -d 'Run as this user (default: root)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l redirect-ports     -d 'Redirect ports below 1024 to unprivileged range'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l isolated           -d 'Isolated mode: no host env vars or Termux paths'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l minimal            -d 'Like --isolated but also disables Android system bindings'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l shared-home        -d 'Mount Termux home inside the container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l termux-home        -d 'Alias for --shared-home'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l shared-tmp         -d 'Share /tmp with the host'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l shared-x11         -d 'Share the X11 socket (/tmp/.X11-unix)'
complete -c proot-distro -n '__fish_seen_subcommand_from run' \
    -l bind            -r -d 'Bind-mount PATH[:DEST] into the container (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l no-link2symlink    -d 'Disable proot link2symlink extension'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l no-sysvipc         -d 'Disable SysV IPC emulation'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l no-kill-on-exit    -d 'Do not kill child processes when the session ends'
complete -c proot-distro -n '__fish_seen_subcommand_from run' \
    -l emulator        -r -d 'Path to QEMU user-mode emulator binary'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l kernel          -r -d 'Fake kernel release string reported to uname'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l hostname        -r -d 'Hostname visible inside the container'
complete -c proot-distro -n '__fish_seen_subcommand_from run' \
    -l work-dir        -r -d 'Initial working directory inside the container'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l env             -r -d 'Set environment variable VAR=VALUE (repeatable)'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -l get-proot-cmd      -d 'Print the proot command line and exit'
complete -c proot-distro -f -n '__fish_seen_subcommand_from run' \
    -s h -l help          -d 'Show help'

# ---------------------------------------------------------------------------
# help / h / he / hel
# ---------------------------------------------------------------------------
complete -c proot-distro -f -n '__fish_seen_subcommand_from help h he hel' \
    -a 'install remove rename reset login list backup restore clear-cache copy sync run' \
    -d 'Topic'

# ---------------------------------------------------------------------------
# pd (same entry point, duplicate all completions)
# ---------------------------------------------------------------------------
complete -c pd -f -n __proot_distro_no_subcommand -a install     -d 'Install a container from a Docker image or local archive'
complete -c pd -f -n __proot_distro_no_subcommand -a add         -d 'Alias for install'
complete -c pd -f -n __proot_distro_no_subcommand -a i           -d 'Alias for install'
complete -c pd -f -n __proot_distro_no_subcommand -a in          -d 'Alias for install'
complete -c pd -f -n __proot_distro_no_subcommand -a ins         -d 'Alias for install'
complete -c pd -f -n __proot_distro_no_subcommand -a remove      -d 'Remove an installed container'
complete -c pd -f -n __proot_distro_no_subcommand -a rm          -d 'Alias for remove'
complete -c pd -f -n __proot_distro_no_subcommand -a rename      -d 'Rename a container'
complete -c pd -f -n __proot_distro_no_subcommand -a reset       -d 'Reinstall a container from its original image'
complete -c pd -f -n __proot_distro_no_subcommand -a login       -d 'Open a shell inside a container'
complete -c pd -f -n __proot_distro_no_subcommand -a sh          -d 'Alias for login'
complete -c pd -f -n __proot_distro_no_subcommand -a list        -d 'List installed containers'
complete -c pd -f -n __proot_distro_no_subcommand -a li          -d 'Alias for list'
complete -c pd -f -n __proot_distro_no_subcommand -a ls          -d 'Alias for list'
complete -c pd -f -n __proot_distro_no_subcommand -a backup      -d 'Backup a container to a tar archive'
complete -c pd -f -n __proot_distro_no_subcommand -a bak         -d 'Alias for backup'
complete -c pd -f -n __proot_distro_no_subcommand -a bkp         -d 'Alias for backup'
complete -c pd -f -n __proot_distro_no_subcommand -a restore     -d 'Restore a container from a tar archive'
complete -c pd -f -n __proot_distro_no_subcommand -a clear-cache -d 'Clear the download cache'
complete -c pd -f -n __proot_distro_no_subcommand -a clear       -d 'Alias for clear-cache'
complete -c pd -f -n __proot_distro_no_subcommand -a cl          -d 'Alias for clear-cache'
complete -c pd -f -n __proot_distro_no_subcommand -a copy        -d 'Copy files between host and container'
complete -c pd -f -n __proot_distro_no_subcommand -a cp          -d 'Alias for copy'
complete -c pd -f -n __proot_distro_no_subcommand -a sync        -d 'Synchronize files between host and container'
complete -c pd -f -n __proot_distro_no_subcommand -a run         -d 'Run the image entrypoint/cmd in a container'
complete -c pd -f -n __proot_distro_no_subcommand -a help        -d 'Show help'
complete -c pd -f -n __proot_distro_no_subcommand -a h           -d 'Alias for help'
complete -c pd -f -n __proot_distro_no_subcommand -a he          -d 'Alias for help'
complete -c pd -f -n __proot_distro_no_subcommand -a hel         -d 'Alias for help'
complete -c pd -f -n __proot_distro_no_subcommand -s h -l help   -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from install add i in ins' -l name           -r -d 'Install under a custom container name'
complete -c pd -f -n '__fish_seen_subcommand_from install add i in ins' -l override-alias -r -d 'Install under a custom container name (alias for --name)'
complete -c pd -f -n '__fish_seen_subcommand_from install add i in ins' -l architecture   -r -d 'Target CPU architecture' -a 'aarch64 arm i686 riscv64 x86_64'
complete -c pd -f -n '__fish_seen_subcommand_from install add i in ins' -s h -l help         -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from remove rm' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from remove rm' -s v -l verbose -d 'Print each removed file'
complete -c pd -f -n '__fish_seen_subcommand_from remove rm' -s h -l help    -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from rename' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from rename' -s h -l help -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from reset' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from reset' -s h -l help -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from login sh' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l user           -r -d 'Run as this user'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l redirect-ports    -d 'Redirect ports below 1024'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l fix-low-ports     -d 'Alias for --redirect-ports'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l isolated          -d 'Isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l minimal           -d 'Minimal isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l shared-home       -d 'Mount Termux home inside container'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l termux-home       -d 'Alias for --shared-home'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l shared-tmp        -d 'Share /tmp with host'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l shared-x11        -d 'Share X11 socket'
complete -c pd -n   '__fish_seen_subcommand_from login sh' -l bind            -r -d 'Bind-mount path (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l no-link2symlink   -d 'Disable link2symlink'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l no-sysvipc        -d 'Disable SysV IPC'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l no-kill-on-exit   -d 'Do not kill on exit'
complete -c pd -n   '__fish_seen_subcommand_from login sh' -l emulator         -r -d 'Emulator binary path'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l kernel          -r -d 'Fake kernel release'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l hostname        -r -d 'Container hostname'
complete -c pd -n   '__fish_seen_subcommand_from login sh' -l work-dir         -r -d 'Working directory'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l env             -r -d 'Environment variable'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -l get-proot-cmd      -d 'Print proot command'
complete -c pd -f -n '__fish_seen_subcommand_from login sh' -s h -l help          -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from list li ls' -s h -l help -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from backup bak bkp' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -n   '__fish_seen_subcommand_from backup bak bkp' -l output     -r -d 'Output archive file'
complete -c pd -f -n '__fish_seen_subcommand_from backup bak bkp' -l compress  -r -d 'Compression type' -a 'gzip bzip2 xz none'
complete -c pd -f -n '__fish_seen_subcommand_from backup bak bkp' -s v -l verbose -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from backup bak bkp' -s h -l help    -d 'Show help'

complete -c pd -n '__fish_seen_subcommand_from restore' -s v -l verbose -d 'Verbose output'
complete -c pd -n '__fish_seen_subcommand_from restore' -s h -l help    -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from clear-cache clear cl' -s v -l verbose -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from clear-cache clear cl' -s h -l help    -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from copy cp' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from copy cp' -s v -l verbose   -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from copy cp' -s m -l move      -d 'Move instead of copy'
complete -c pd -f -n '__fish_seen_subcommand_from copy cp' -s r -l recursive -d 'Recursive copy'
complete -c pd -f -n '__fish_seen_subcommand_from copy cp' -s h -l help      -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from sync' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s v -l verbose   -d 'Verbose output'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -l checksum          -d 'Use CRC32 checksum'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -l delete            -d 'Delete extra destination files'
complete -c pd -f -n '__fish_seen_subcommand_from sync' -s h -l help         -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from run' -a '(__proot_distro_containers)' -d 'Container'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l user           -r -d 'Run as this user'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l redirect-ports    -d 'Redirect ports below 1024'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l isolated          -d 'Isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l minimal           -d 'Minimal isolated mode'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l shared-home       -d 'Mount Termux home inside container'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l termux-home       -d 'Alias for --shared-home'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l shared-tmp        -d 'Share /tmp with host'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l shared-x11        -d 'Share X11 socket'
complete -c pd -n   '__fish_seen_subcommand_from run' -l bind            -r -d 'Bind-mount path (repeatable)'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l no-link2symlink   -d 'Disable link2symlink'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l no-sysvipc        -d 'Disable SysV IPC'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l no-kill-on-exit   -d 'Do not kill on exit'
complete -c pd -n   '__fish_seen_subcommand_from run' -l emulator         -r -d 'Emulator binary path'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l kernel          -r -d 'Fake kernel release'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l hostname        -r -d 'Container hostname'
complete -c pd -n   '__fish_seen_subcommand_from run' -l work-dir         -r -d 'Working directory'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l env             -r -d 'Environment variable'
complete -c pd -f -n '__fish_seen_subcommand_from run' -l get-proot-cmd      -d 'Print proot command'
complete -c pd -f -n '__fish_seen_subcommand_from run' -s h -l help          -d 'Show help'

complete -c pd -f -n '__fish_seen_subcommand_from help h he hel' \
    -a 'install remove rename reset login list backup restore clear-cache copy sync run' -d 'Topic'
