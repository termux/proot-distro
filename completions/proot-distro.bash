_proot-distro() {
	local cur prev words cword
	_init_completion || return

	local pd_commands="help backup install list login remove rename reset restore clear-cache"

	if [ "$cword" == "1" ]; then
		COMPREPLY=($(compgen -W "$pd_commands" -- "$cur"))
	fi
}

complete -F _proot-distro proot-distro
