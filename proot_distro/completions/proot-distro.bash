# Bash completion for proot-distro and pd
#
# Install system-wide:
#   cp proot-distro.bash /usr/share/bash-completion/completions/proot-distro
# Install for current user:
#   mkdir -p ~/.local/share/bash-completion/completions
#   cp proot-distro.bash ~/.local/share/bash-completion/completions/proot-distro

_proot_distro_get_containers() {
    local dir
    if [[ -n "${TERMUX_PREFIX}" ]]; then
        dir="${TERMUX_PREFIX}/var/lib/proot-distro/containers"
    elif [[ -n "${ANDROID_ROOT}" ]]; then
        dir="/data/data/com.termux/files/usr/var/lib/proot-distro/containers"
    else
        dir="${XDG_DATA_HOME:-${HOME}/.local/share}/proot-distro/containers"
    fi
    if [[ -d "${dir}" ]]; then
        local d
        for d in "${dir}"/*/; do
            [[ -d "${d}rootfs" ]] && printf '%s\n' "${d%/}" | sed 's|.*/||'
        done
    fi
}

_proot_distro() {
    local cur prev words cword
    _init_completion || return

    local -r _all_commands="install add i in ins remove rm rename reset login sh
        list li ls backup bak bkp restore clear-cache clear cl copy cp sync run
        help h he hel"

    # Complete the subcommand itself
    if [[ ${cword} -eq 1 ]]; then
        COMPREPLY=($(compgen -W "${_all_commands}" -- "${cur}"))
        return
    fi

    local command="${words[1]}"
    case "${command}" in
        add|i|in|ins)   command="install" ;;
        rm)             command="remove" ;;
        sh)             command="login" ;;
        li|ls)          command="list" ;;
        bak|bkp)        command="backup" ;;
        clear|cl)       command="clear-cache" ;;
        cp)             command="copy" ;;
        h|he|hel)       command="help" ;;
    esac

    case "${command}" in

        # -----------------------------------------------------------------------
        install)
            case "${prev}" in
                --name|--override-alias)
                    return ;;
                --architecture)
                    COMPREPLY=($(compgen -W "aarch64 arm i686 riscv64 x86_64" -- "${cur}"))
                    return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--name --override-alias --architecture --help" -- "${cur}"))
            elif [[ "${cur}" == /* || "${cur}" == ./* || "${cur}" == ../* ]]; then
                _filedir
            fi
            ;;

        # -----------------------------------------------------------------------
        remove)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--verbose --help" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        rename)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--help" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        reset)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--help" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        login)
            # After --, complete host-side commands
            local i
            for ((i = 2; i < cword; i++)); do
                if [[ "${words[i]}" == "--" ]]; then
                    _command_offset $((i + 1))
                    return
                fi
            done
            case "${prev}" in
                --user)      return ;;
                --bind)      _filedir;   return ;;
                --emulator)  _filedir;   return ;;
                --kernel)    return ;;
                --hostname)  return ;;
                --work-dir)  _filedir -d; return ;;
                --env)       return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "
                    --user --redirect-ports --fix-low-ports
                    --isolated --minimal
                    --shared-home --termux-home
                    --shared-tmp --shared-x11
                    --bind --no-link2symlink --no-sysvipc --no-kill-on-exit
                    --emulator --kernel --hostname --work-dir
                    --env --get-proot-cmd --help
                " -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        list)
            COMPREPLY=($(compgen -W "--help" -- "${cur}"))
            ;;

        # -----------------------------------------------------------------------
        backup)
            case "${prev}" in
                --output)
                    _filedir
                    return ;;
                --compress)
                    COMPREPLY=($(compgen -W "gzip bzip2 xz none" -- "${cur}"))
                    return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--output --compress --verbose --help" -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        restore)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--verbose --help" -- "${cur}"))
            else
                _filedir '@(tar|tar.gz|tgz|tar.bz2|tbz2|tar.xz|txz)'
            fi
            ;;

        # -----------------------------------------------------------------------
        clear-cache)
            COMPREPLY=($(compgen -W "--verbose --help" -- "${cur}"))
            ;;

        # -----------------------------------------------------------------------
        copy)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--verbose --move --recursive --help" -- "${cur}"))
            else
                # Support container:path notation: complete container names
                # (no colon yet) or paths (colon already present → filesystem)
                if [[ "${cur}" == *:* ]]; then
                    _filedir
                else
                    local containers
                    containers=$(_proot_distro_get_containers)
                    COMPREPLY=($(compgen -W "${containers}" -- "${cur}"))
                    # Also allow plain host paths
                    local -a files
                    _filedir
                    COMPREPLY+=("${files[@]}")
                fi
            fi
            ;;

        # -----------------------------------------------------------------------
        sync)
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "--verbose --checksum --delete --help" -- "${cur}"))
            else
                if [[ "${cur}" == *:* ]]; then
                    _filedir
                else
                    local containers
                    containers=$(_proot_distro_get_containers)
                    COMPREPLY=($(compgen -W "${containers}" -- "${cur}"))
                    local -a files
                    _filedir
                    COMPREPLY+=("${files[@]}")
                fi
            fi
            ;;

        # -----------------------------------------------------------------------
        run)
            # After --, complete host-side commands
            local i
            for ((i = 2; i < cword; i++)); do
                if [[ "${words[i]}" == "--" ]]; then
                    _command_offset $((i + 1))
                    return
                fi
            done
            case "${prev}" in
                --user)      return ;;
                --bind)      _filedir;   return ;;
                --emulator)  _filedir;   return ;;
                --kernel)    return ;;
                --hostname)  return ;;
                --work-dir)  _filedir -d; return ;;
                --env)       return ;;
            esac
            if [[ "${cur}" == -* ]]; then
                COMPREPLY=($(compgen -W "
                    --user --redirect-ports
                    --isolated --minimal
                    --shared-home --termux-home
                    --shared-tmp --shared-x11
                    --bind --no-link2symlink --no-sysvipc --no-kill-on-exit
                    --emulator --kernel --hostname --work-dir
                    --env --get-proot-cmd --help
                " -- "${cur}"))
            else
                COMPREPLY=($(compgen -W "$(_proot_distro_get_containers)" -- "${cur}"))
            fi
            ;;

        # -----------------------------------------------------------------------
        help)
            local topics="install remove rename reset login list backup restore clear-cache copy sync run"
            COMPREPLY=($(compgen -W "${topics}" -- "${cur}"))
            ;;

    esac
}

complete -F _proot_distro proot-distro
complete -F _proot_distro pd
