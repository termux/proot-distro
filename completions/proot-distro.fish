# Completions for proot-distro
# https://github.com/termux/proot-distro

function __fish_pd_available_dists
	set -l pd_plugin_dir @TERMUX_PREFIX@/etc/proot-distro
	if not test -d $pd_plugin_dir
		return
	end
	path basename $pd_plugin_dir/*.sh | path change-extension ""
end

function __fish_pd_installed_dists
	set -l pd_rootfs_dir @TERMUX_PREFIX@/var/lib/proot-distro/installed-rootfs
	if not test -d $pd_rootfs_dir
		return
	end
	path basename $pd_rootfs_dir/*/
end

set -l backup backup bak bkp
set -l install install i in ins add
set -l list list li ls
set -l login login sh
set -l remove remove rm
set -l rename rename mv
set -l clear_cache clear-cache clear cl
set -l copy copy cp

set -l __fish_pd_commands help $backup $install $list $login $remove $rename reset restore $clear_cache $copy

complete -c proot-distro -f

# Subcommands
complete -c proot-distro -kf -n __fish_use_subcommand -a copy -d "Copy files from/to distribution"
complete -c proot-distro -kf -n __fish_use_subcommand -a clear-cache -d "Clear cache of downloaded files"
complete -c proot-distro -kf -n __fish_use_subcommand -a restore -d "Restore a specified distribution"
complete -c proot-distro -kf -n __fish_use_subcommand -a reset -d "Reinstall a specified distribution"
complete -c proot-distro -kf -n __fish_use_subcommand -a rename -d "Rename installed distribution"
complete -c proot-distro -kf -n __fish_use_subcommand -a remove -d "Delete a specified distribution"
complete -c proot-distro -kf -n __fish_use_subcommand -a login -d "Start a shell for the given distribution"
complete -c proot-distro -kf -n __fish_use_subcommand -a list -d "List available distributions"
complete -c proot-distro -kf -n __fish_use_subcommand -a install -d "Install a specified distribution"
complete -c proot-distro -kf -n __fish_use_subcommand -a backup -d "Backup a specified distribution"
complete -c proot-distro -kf -n __fish_use_subcommand -a help -d "Show help information"

# Subcommand arguments
complete -c proot-distro -F -n "__fish_seen_subcommand_from restore $copy"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $backup $login $remove $rename reset" \
	-n "not __fish_seen_subcommand_from (__fish_pd_installed_dists)" \
	-a "(__fish_pd_installed_dists)"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $install" \
	-n "not __fish_seen_subcommand_from (__fish_pd_available_dists)" \
	-a "(__fish_pd_available_dists)"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $rename" \
	-n "__fish_seen_subcommand_from (__fish_pd_installed_dists)" \
	-n "not __fish_seen_subcommand_from new-dist-name" \
	-a new-dist-name

# Options
complete -c proot-distro -f -n "__fish_seen_subcommand_from $__fish_pd_commands" -l help -d "Show help information"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $list $copy" -l verbose

# Backup options
complete -c proot-distro -Fr -n "__fish_seen_subcommand_from $backup" -l output -d "Write tarball to specified file"

# Install options
complete -c proot-distro -x -n "__fish_seen_subcommand_from $install" \
	-l override-alias -a new-dist-name -d "Set a custom alias for installed distribution"

# Login options
complete -c proot-distro -x -n "__fish_seen_subcommand_from $login" -l user -a username -d "Login as specified user instead of root"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $login" -l fix-low-ports -d "Force redirect low networking ports to a high number"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $login" -l isolated -d "Do not mount host volumes inside proot environment"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $login" -l termux-home -d "Mount Termux home directory as user home inside proot"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $login" -l shared-tmp -d "Mount Termux temp directory to /tmp"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $login" -l no-link2symlink -d "Disable PRoot link2symlink extension"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $login" -l no-sysvipc -d "Disable PRoot System V IPC emulation"
complete -c proot-distro -f -n "__fish_seen_subcommand_from $login" -l no-kill-on-exit -d "Do not kill processes when shell session terminates"
complete -c proot-distro -x -n "__fish_seen_subcommand_from $login" -l kernel -a 6.17.0-PRoot-Distro -d "Customize Linux kernel release string"
complete -c proot-distro -x -n "__fish_seen_subcommand_from $login" -l hostname -a localhost -d "Customize system host name"
complete -c proot-distro -Fr -n "__fish_seen_subcommand_from $login" -l bind -d "Create a custom file system path binding"
complete -c proot-distro -Fr -n "__fish_seen_subcommand_from $login" -l work-dir -d "Set the working directory to given value"
complete -c proot-distro -Fr -n "__fish_seen_subcommand_from $login" -l env -a ENVVAR=value -d "Set environment variable"
