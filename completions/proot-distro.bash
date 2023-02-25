__proot_distro_get_list()
{
	local list_output="$(PROOT_DISTRO_FORCE_NO_COLORS=true proot-distro list 2>&1)"

	local list_all="$(awk '{
		if($1 == "Alias:") {
			print $2
		}
	}' <<<"$list_output")"

	if [ $1 == all ]; then
		echo "$list_all"
		return
	fi

	local list_installed="$(awk '{
		if($1 == "Alias:"){
			alias = $2
		}else if($1 == "Status:" && $2 == "installed"){
			print alias
		}
	}' <<<"$list_output")"

	if [ $1 == installed ]; then
		echo "$list_installed"
		return
	fi

	local list_not_installed="$(comm -23 <(echo "$list_all") <(echo "$list_installed"))"

	if [ $1 == not_installed ]; then
		echo "$list_not_installed"
		return
	fi
}

__proot_distro_install()
{
	case $prev in
		--override-alias) ;;
		*) COMPREPLY=( $(compgen -W "$(__proot_distro_get_list not_installed) --help --override-alias" -- "$cur") )
	esac
}

__proot_distro_remove()
{
	COMPREPLY=( $(compgen -W "$(__proot_distro_get_list installed) --help" -- "$cur") )
}

__proot_distro_reset()
{
	COMPREPLY=( $(compgen -W "$(__proot_distro_get_list installed) --help" -- "$cur") )
}

__proot_distro_login()
{
	local i distro_name
	for ((i=2; i < COMP_CWORD; ++i)); do
		case ${COMP_WORDS[i]} in
			--) return;;
			-*) ;;
			*)
				if [ -z "$distro_name" ]; then
					distro_name="${COMP_WORDS[i]}"
				fi
				;;
		esac
	done

	case $prev in
		--bind)
			if [[ "$cur" != *":"* ]]; then
				COMPREPLY=( $(compgen -df -- "$cur") )
			else
				local bind_dst="${cur#*:}"
				COMPREPLY=( $(cd "$INSTALLED_ROOTFS_DIR/$distro_name" 2>/dev/null && compgen -df -P "/" -- "${bind_dst#/}") )
			fi
			;;
		--user|--kernel) ;;
		*) COMPREPLY=( $(compgen -W "$([ -z "$distro_name" ] && __proot_distro_get_list installed) -- --help --fix-low-ports --isolated --termux-home --shared-tmp --bind --no-link2symlink --no-sysvipc --no-kill-on-exit --user --kernel" -- "$cur") )
	esac
}

__proot_distro_backup()
{
	case $prev in
		--output)
			COMPREPLY=( $(compgen -f -- "$cur") )
			;;
		*) COMPREPLY=( $(compgen -W "$(__proot_distro_get_list installed) --help --output" -- "$cur") )
	esac
}

__proot_distro_restore()
{
	COMPREPLY=( $(compgen -W "--help" -f -- "$cur") )
}

__proot_distro_clear_cache()
{
	COMPREPLY=( $(compgen -W "--help" -- "$cur") )
}

_proot_distro()
{
	local cur prev words cword
	_init_completion || return
	_get_comp_words_by_ref -n : cur prev words cword
	compopt -o filenames

	local INSTALLED_ROOTFS_DIR="@TERMUX_PREFIX@/var/lib/proot-distro/installed-rootfs"

	if [ $COMP_CWORD -eq 1 ]; then
		COMPREPLY=( $(compgen -W "help backup install list login remove clear-cache reset restore" -- "$cur") )
		return
	fi

	case "${COMP_WORDS[1]}" in
		-h|--help|help) ;;
		backup) __proot_distro_backup;;
		install) __proot_distro_install;;
		list) ;;
		login) __proot_distro_login;;
		remove) __proot_distro_remove;;
		clear-cache) __proot_distro_clear_cache;;
		reset) __proot_distro_reset;;
		restore) __proot_distro_restore;;
		*) ;;
	esac
}

complete -F _proot_distro proot-distro
