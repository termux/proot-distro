_proot-distro() {
	local cur prev words cword
	_init_completion || return

	local pd_commands="help backup install list login remove rename reset restore clear-cache"
	local pd_dists=$(cd @TERMUX_PREFIX@/etc/proot-distro; find . -maxdepth 1 -iname "*.sh" | cut -d/ -f2 | sed -E 's/(.*)\..*/\1/g')

	if [ "$cword" == "1" ]; then
		COMPREPLY=($(compgen -W "$pd_commands" -- "$cur"))
	else
		case "${words[1]}" in
			backup)
				if [[ $prev = "${words[1]}" ]]; then
					COMPREPLY=($(compgen -W "${pd_dists} --help --output" -- "$cur"))
				elif [[ $prev = "--output" ]]; then
					_filedir
				fi
			;;
		esac
	fi
}

complete -F _proot-distro proot-distro
