_ls_available_dists() {
	if [ ! -e "@TERMUX_PREFIX@/etc/proot-distro" ]; then
		return
	fi
	cd "@TERMUX_PREFIX@/etc/proot-distro"
	find . -mindepth 1 -maxdepth 1 -iname "*.sh" | cut -d/ -f2 | sed -E 's/(.*)\..*/\1/g'
}

_ls_installed_dists() {
	if [ ! -e "@TERMUX_PREFIX@/var/lib/proot-distro/installed-rootfs" ]; then
		return
	fi
	cd "@TERMUX_PREFIX@/var/lib/proot-distro/installed-rootfs"
	find . -mindepth 1 -maxdepth 1 -type d | cut -d/ -f2
}

_proot-distro() {
	local cur prev words cword
	_init_completion || return

	local pd_commands="help backup install list login remove rename reset restore clear-cache copy"
	local pd_available_dists=$(_ls_available_dists)
	local pd_installed_dists=$(_ls_installed_dists)

	if [ "$cword" == "1" ]; then
		COMPREPLY=($(compgen -W "$pd_commands" -- "$cur"))
	else
		case "${words[1]}" in
			backup|bak|bkp)
				if [ "$prev" = "--output" ]; then
					_filedir
				else
					COMPREPLY=($(compgen -W "${pd_installed_dists} --output" -- "$cur"))
				fi
			;;
			install|i|in|ins|add)
				if [ "$prev" = "--override-alias" ]; then
					COMPREPLY=($(compgen -W "new-dist-name" -- "$cur"))
				else
					COMPREPLY=($(compgen -W "${pd_available_dists} --override-alias" -- "$cur"))
				fi
			;;
			list|li|ls)
				COMPREPLY=($(compgen -W "--verbose" -- "$cur"))
			;;
			login|sh)
				if [ $prev = "--user" ]; then
					COMPREPLY=($(compgen -W "root" -- "$cur"))
				elif [ $prev = "--bind" ]; then
					COMPREPLY=($(compgen -W "/src:/dest" -- "$cur"))
				elif [ $prev = "--kernel" ]; then
					COMPREPLY=($(compgen -W "6.17.0-PRoot-Distro" -- "$cur"))
				elif [ $prev = "--hostname" ]; then
					COMPREPLY=($(compgen -W "localhost" -- "$cur"))
				elif [ $prev = "--work-dir" ]; then
					COMPREPLY=($(compgen -W "/" -- "$cur"))
				elif [ $prev = "--env" ]; then
					COMPREPLY=($(compgen -W "ENVVAR=value" -- "$cur"))
				else
					COMPREPLY=($(compgen -W "${pd_installed_dists} --user --fix-low-ports --isolated --termux-home --shared-tmp --bind --no-link2symlink --no-sysvipc --no-kill-on-exit --no-arch-warning --kernel --hostname --work-dir --env" -- "$cur"))
				fi
			;;
			remove|rm)
				COMPREPLY=($(compgen -W "${pd_installed_dists}" -- "$cur"))
			;;
			rename|mv)
				if [ "$prev" = "rename" ] || [ "$prev" = "mv" ]; then
					COMPREPLY=($(compgen -W "${pd_installed_dists}" -- "$cur"))
				else
					COMPREPLY=($(compgen -W "new-dist-name" -- "$cur"))
				fi
			;;
			reset)
				COMPREPLY=($(compgen -W "${pd_installed_dists}" -- "$cur"))
			;;
			restore)
				_filedir
			;;
			copy|cp)
				_filedir
			;;
		esac
	fi
}

complete -F _proot-distro proot-distro pd
