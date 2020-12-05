#!@TERMUX_PREFIX@/bin/bash
##
## Script for managing proot'ed Linux distribution installations in Termux.
##
## Copyright (C) 2020 Leonid Pliushch <leonid.pliushch@gmail.com>
##
## This program is free software: you can redistribute it and/or modify
## it under the terms of the GNU General Public License as published by
## the Free Software Foundation, either version 3 of the License, or
## (at your option) any later version.
##
## This program is distributed in the hope that it will be useful,
## but WITHOUT ANY WARRANTY; without even the implied warranty of
## MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
## GNU General Public License for more details.
##
## You should have received a copy of the GNU General Public License
## along with this program. If not, see <http://www.gnu.org/licenses/>.
##

PROGRAM_VERSION="1.1"

#############################################################################
#
# GLOBAL ENVIRONMENT AND INSTALLATION-SPECIFIC CONFIGURATION
#

set -e -u

PROGRAM_NAME="proot-distro"

# Where distribution plug-ins are stored.
DISTRO_PLUGINS_DIR="@TERMUX_PREFIX@/etc/proot-distro"

# Base directory where script keeps runtime data.
RUNTIME_DIR="@TERMUX_PREFIX@/var/lib/proot-distro"

# Where rootfs tarballs are downloaded.
DOWNLOAD_CACHE_DIR="${RUNTIME_DIR}/dlcache"

# Where extracted rootfs are stored.
INSTALLED_ROOTFS_DIR="${RUNTIME_DIR}/installed-rootfs"

# Colors.
if [ -n "$(command -v tput)" ] && [ $(tput colors) -ge 8 ] && [ -z "${PROOT_DISTRO_FORCE_NO_COLORS-}" ]; then
	RST="$(tput sgr0)"
	RED="${RST}$(tput setaf 1)"
	BRED="${RST}$(tput bold)$(tput setaf 1)"
	GREEN="${RST}$(tput setaf 2)"
	YELLOW="${RST}$(tput setaf 3)"
	BYELLOW="${RST}$(tput bold)$(tput setaf 3)"
	BLUE="${RST}$(tput setaf 4)"
	CYAN="${RST}$(tput setaf 6)"
	BCYAN="${RST}$(tput bold)$(tput setaf 6)"
	ICYAN="${RST}$(tput sitm)$(tput setaf 6)"
else
	RED=""
	BRED=""
	GREEN=""
	YELLOW=""
	BYELLOW=""
	BLUE=""
	CYAN=""
	BCYAN=""
	ICYAN=""
	RST=""
fi

#############################################################################
#
# ANTI-ROOT FUSE
#
# This script should never be executed as root as can mess up the ownership,
# and SELinux labels in $PREFIX.
#
if [ "$(id -u)" = "0" ]; then
	echo
	echo -e "${BRED}Error: utility '${YELLOW}${PROGRAM_NAME}${BRED}' should not be used as root.${RST}"
	echo
	exit 1
fi

#############################################################################
#
# FUNCTION TO CHECK WHETHER DISTRIBUTION IS INSTALLED
#
# This is done by checking the presence of /bin directory in rootfs.
#
# Accepted arguments: $1 - name of distribution.
#
is_distro_installed() {
	if [ -e "${INSTALLED_ROOTFS_DIR}/${1}/bin" ]; then
		return 0
	else
		return 1
	fi
}

#############################################################################
#
# FUNCTION TO INSTALL THE SPECIFIED DISTRIBUTION
#
# Installs the Linux distribution by the following algorithm:
#
#  1. Checks whether requested distribution is supported, if yes - continue.
#  2. Checks whether requested distribution is installed, if not - continue.
#  3. Source the distribution configuration plug-in which contains the
#     functionality necessary for installation. It must define at least
#     get_download_url() function which returns a download URL.
#  4. Download the rootfs archive, if it is not available in cache.
#  5. Extract the rootfs by using `tar` running under proot with link2symlink
#     extension.
#  6. Write environment variables configuration to /etc/profile.d/termux-proot.sh.
#     If profile.d directory is not available, append to /etc/profile.
#  7. Write default /etc/resolv.conf.
#  8. Write default /etc/hosts.
#  9. Add missing Android specific UIDs/GIDs to user database.
#  10. Execute optional setup hook (distro_setup) if present.
#
# Accepted arguments: $1 - distribution name.
#
command_install() {
	local distro_name
	local override_alias
	local distro_plugin_script

	while (($# >= 1)); do
		case "$1" in
			--)
				shift 1
				break
				;;
			--help)
				command_install_help
				return 0
				;;
			--override-alias)
				if [ $# -ge 2 ]; then
					shift 1

					if [ -z "$1" ]; then
						echo
						echo -e "${BRED}Error: argument to option '${YELLOW}--override-alias${BRED}' should not be empty.${RST}"
						command_install_help
						return 1
					fi

					override_alias="$1"
				else
					echo
					echo -e "${BRED}Error: option '${YELLOW}$1${BRED}' requires an argument.${RST}"
					command_install_help
					return 1
				fi
				;;
			-*)
				echo
				echo -e "${BRED}Error: unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_install_help
				return 1
				;;
			*)
				if [ -z "${distro_name-}" ]; then
					distro_name="$1"
				else
					echo
					echo -e "${BRED}Error: unknown option '${YELLOW}${1}${BRED}'.${RST}"
					echo
					echo -e "${BRED}Error: you have already set distribution as '${YELLOW}${distro_name}${BRED}'.${RST}"
					command_install_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -z "${distro_name-}" ]; then
		echo
		echo -e "${BRED}Error: distribution alias is not specified.${RST}"
		command_install_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		echo
		echo -e "${BRED}Error: unknown distribution '${YELLOW}${distro_name}${BRED}' was requested to be installed.${RST}"
		echo
		echo -e "${CYAN}Run '${GREEN}${PROGRAM_NAME} list${CYAN}' to see the supported distributions.${RST}"
		echo
		return 1
	fi

	if [ -n "${override_alias-}" ]; then
		if [ ! -e "${DISTRO_PLUGINS_DIR}/${override_alias}.sh" ]; then
			echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating file '${DISTRO_PLUGINS_DIR}/${override_alias}.sh'...${RST}"
			distro_plugin_script="${DISTRO_PLUGINS_DIR}/${override_alias}.override.sh"
			cp "${DISTRO_PLUGINS_DIR}/${distro_name}.sh" "${distro_plugin_script}"
			sed -i "s/^\(DISTRO_NAME=\)\(.*\)\$/\1\"${SUPPORTED_DISTRIBUTIONS["$distro_name"]} (override)\"/g" "${distro_plugin_script}"
			SUPPORTED_DISTRIBUTIONS["${override_alias}"]="${SUPPORTED_DISTRIBUTIONS["$distro_name"]}"
			distro_name="${override_alias}"
		else
			echo
			echo -e "${BRED}Error: you cannot use value '${YELLOW}${override_alias}${BRED}' as alias override.${RST}"
			echo
			return 1
		fi
	else
		distro_plugin_script="${DISTRO_PLUGINS_DIR}/${distro_name}.sh"

		# Try an alternate distribution name.
		if [ ! -f "${distro_plugin_script}" ]; then
			distro_plugin_script="${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh"
		fi
	fi

	if is_distro_installed "$distro_name"; then
		echo
		echo -e "${BRED}Error: distribution '${YELLOW}${distro_name}${BRED}' is already installed.${RST}"
		echo
		echo -e "${CYAN}Log in:     ${GREEN}${PROGRAM_NAME} login ${distro_name}${RST}"
		echo -e "${CYAN}Reinstall:  ${GREEN}${PROGRAM_NAME} reset ${distro_name}${RST}"
		echo -e "${CYAN}Uninstall:  ${GREEN}${PROGRAM_NAME} remove ${distro_name}${RST}"
		echo
		return 1
	fi

	if [ -f "${distro_plugin_script}" ]; then
		echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Installing ${YELLOW}${SUPPORTED_DISTRIBUTIONS["$distro_name"]}${CYAN}...${RST}"

		if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
			echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating directory '${INSTALLED_ROOTFS_DIR}/${distro_name}'...${RST}"
			mkdir -m 755 -p "${INSTALLED_ROOTFS_DIR}/${distro_name}"
		fi

		if [ -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/.l2s" ]; then
			export PROOT_L2S_DIR="${INSTALLED_ROOTFS_DIR}/${distro_name}/.l2s"
		fi

		# We need this to disable the preloaded libtermux-exec.so library
		# which redefines 'execve()' implementation.
		unset LD_PRELOAD

		# Needed for compatibility with some devices.
		#export PROOT_NO_SECCOMP=1

		# Some distributions store rootfs in subdirectory - in this case
		# this variable should be set to 1.
		DISTRO_TARBALL_STRIP_OPT=0

		# Distribution plug-in contains steps on how to get download URL
		# and further post-installation configuration.
		source "${distro_plugin_script}"

		local download_url
		if declare -f -F get_download_url >/dev/null 2>&1; then
			download_url=$(get_download_url)
		else
			echo
			echo -e "${BRED}Error: get_download_url() is not defined in ${distro_plugin_script}${RST}"
			echo
			return 1
		fi

		if [ -z "$download_url" ]; then
			echo -e "${BLUE}[${RED}!${BLUE}] ${CYAN}Sorry, but distribution download URL is not defined for your CPU architecture '$(uname -m)'.${RST}"
			return 1
		fi

		if [ ! -d "$DOWNLOAD_CACHE_DIR" ]; then
			echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating directory '$DOWNLOAD_CACHE_DIR'...${RST}"
			mkdir -p "$DOWNLOAD_CACHE_DIR"
		fi

		local tarball_name
		tarball_name=$(basename "$download_url")

		if [ ! -f "${DOWNLOAD_CACHE_DIR}/${tarball_name}" ]; then
			echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Downloading rootfs tarball...${RST}"

			# Using temporary file as script can't distinguish the partially
			# downloaded file from the complete. Useful in case if curl will
			# fail for some reason.
			echo
			rm -f "${DOWNLOAD_CACHE_DIR}/${tarball_name}.tmp"
			if ! curl --fail --retry 5 --retry-connrefused --retry-delay 5 --location \
				--output "${DOWNLOAD_CACHE_DIR}/${tarball_name}.tmp" "$download_url"; then
				echo -e "${BLUE}[${RED}!${BLUE}] ${CYAN}Download failure, please check your network connection.${RST}"
				rm -f "${DOWNLOAD_CACHE_DIR}/${tarball_name}.tmp"
				return 1
			fi
			echo

			# If curl finished successfully, rename file to original.
			mv -f "${DOWNLOAD_CACHE_DIR}/${tarball_name}.tmp" "${DOWNLOAD_CACHE_DIR}/${tarball_name}"
		else
			echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Using cached rootfs tarball...${RST}"
		fi

		echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Extracting rootfs, please wait...${RST}"
		# --exclude='dev'||: - need to exclude /dev directory which may contain device files.
		# --delay-directory-restore - set directory permissions only when files were extracted
		#                             to avoid issues with Arch Linux bootstrap archives.
		proot --link2symlink \
			tar -C "${INSTALLED_ROOTFS_DIR}/${distro_name}" --warning=no-unknown-keyword \
			--delay-directory-restore --preserve-permissions --strip="$DISTRO_TARBALL_STRIP_OPT" \
			-xf "${DOWNLOAD_CACHE_DIR}/${tarball_name}" --exclude='dev'||:

		# Write important environment variables to profile file as /bin/login does not
		# preserve them.
		local profile_script
		if [ -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile.d" ]; then
			profile_script="${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile.d/termux-proot.sh"
		else
			profile_script="${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile"
		fi
		echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Writing '$profile_script'...${RST}"
		local LIBGCC_S_PATH
		LIBGCC_S_PATH="/$(cd ${INSTALLED_ROOTFS_DIR}/${distro_name}; find usr/lib/ -name libgcc_s.so.1)"
		cat <<- EOF >> "$profile_script"
		export ANDROID_ART_ROOT=${ANDROID_ART_ROOT-}
		export ANDROID_DATA=${ANDROID_DATA-}
		export ANDROID_I18N_ROOT=${ANDROID_I18N_ROOT-}
		export ANDROID_ROOT=${ANDROID_ROOT-}
		export ANDROID_RUNTIME_ROOT=${ANDROID_RUNTIME_ROOT-}
		export ANDROID_TZDATA_ROOT=${ANDROID_TZDATA_ROOT-}
		export BOOTCLASSPATH=${BOOTCLASSPATH-}
		export COLORTERM=${COLORTERM-}
		export DEX2OATBOOTCLASSPATH=${DEX2OATBOOTCLASSPATH-}
		export EXTERNAL_STORAGE=${EXTERNAL_STORAGE-}
		export LANG=C.UTF-8
		export PATH=\${PATH}:/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
		export PREFIX=${PREFIX-/data/data/com.termux/files/usr}
		export TERM=${TERM-xterm-256color}
		export TMPDIR=/tmp
		EOF
		if [ "${LIBGCC_S_PATH}" != "/" ]; then
			echo "${LIBGCC_S_PATH}" >> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/ld.so.preload"
			chmod 644 "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/ld.so.preload"
		fi
		unset LIBGCC_S_PATH

		# /etc/resolv.conf may not be configured, so write in it our configuraton.
		echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Writing resolv.conf file (NS 1.1.1.1/1.0.0.1)...${RST}"
		rm -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/resolv.conf"
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/resolv.conf"
		nameserver 1.1.1.1
		nameserver 1.0.0.1
		EOF

		# /etc/hosts may be empty by default on some distributions.
		echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Writing hosts file...${RST}"
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/hosts"
		# IPv4.
		127.0.0.1   localhost.localdomain localhost

		# IPv6.
		::1         localhost.localdomain localhost ipv6-localhost ipv6-loopback
		fe00::0     ipv6-localnet
		ff00::0     ipv6-mcastprefix
		ff02::1     ipv6-allnodes
		ff02::2     ipv6-allrouters
		ff02::3     ipv6-allhosts
		EOF

		# Add Android-specific UIDs/GIDs to /etc/group and /etc/gshadow.
		echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Registering Android-specific UIDs and GIDs...${RST}"
		echo "aid_$(id -un):x:$(id -u):$(id -g):Android user:/:/usr/sbin/nologin" >> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/passwd"
		echo "aid_$(id -un):*:18446:0:99999:7:::" >> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/shadow"
		local g
		for g in $(id -G); do
			echo "aid_$(id -gn "$g"):x:${g}:root,aid_$(id -un)" >> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/group"
			if [ -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/gshadow" ]; then
				echo "aid_$(id -gn "$g"):*::root,aid_$(id -un)" >> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/gshadow"
			fi
		done

		# Ensure that proot will be able to bind fake /proc entries.
		setup_fake_proc

		# Run optional distro-specific hook.
		if declare -f -F distro_setup >/dev/null 2>&1; then
			echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Running distro-specific configuration steps...${RST}"
			(cd "${INSTALLED_ROOTFS_DIR}/${distro_name}"
				distro_setup
			)
		fi

		echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Installation finished.${RST}"
		echo
		echo -e "${CYAN}Now run '${GREEN}$PROGRAM_NAME login $distro_name${CYAN}' to log in.${RST}"
		echo
		return 0
	else
		echo -e "${BLUE}[${RED}!${BLUE}] ${CYAN}Cannot find '${distro_plugin_script}' which contains distro-specific install functions.${RST}"
		return 1
	fi
}

# Special function for executing a command in rootfs.
# Can be used only inside distro_setup().
run_proot_cmd() {
	if [ -z "${distro_name-}" ]; then
		echo
		echo -e "${BRED}Error: called run_proot_cmd() but \${distro_name} is not set. Possible cause: using run_proot_cmd() outside of distro_setup()?${RST}"
		echo
		return 1
	fi

	proot \
		--kernel-release=5.4.0-faked \
		--link2symlink \
		--kill-on-exit \
		--rootfs="${INSTALLED_ROOTFS_DIR}/${distro_name}" \
		--root-id \
		--cwd=/root \
		--bind=/dev \
		--bind="/dev/urandom:/dev/random" \
		--bind=/proc \
		--bind="/proc/self/fd:/dev/fd" \
		--bind="/proc/self/fd/0:/dev/stdin" \
		--bind="/proc/self/fd/1:/dev/stdout" \
		--bind="/proc/self/fd/2:/dev/stderr" \
		--bind=/sys \
		--bind="${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.loadavg:/proc/loadavg" \
		--bind="${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.stat:/proc/stat" \
		--bind="${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.uptime:/proc/uptime" \
		--bind="${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.version:/proc/version" \
		/usr/bin/env -i \
			"HOME=/root" \
			"LANG=C.UTF-8" \
			"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
			"TERM=$TERM" \
			"TMPDIR=/tmp" \
			"$@"
}

# A function for preparing fake content for certain /proc
# entries which are known to be restricted on Android.
setup_fake_proc() {
	mkdir -p "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc"
	chmod 700 "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc"

	if [ ! -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.loadavg" ]; then
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.loadavg"
		0.54 0.41 0.30 1/931 370386
		EOF
	fi

	if [ ! -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.stat" ]; then
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.stat"
		cpu  1050008 127632 898432 43828767 37203 63 99244 0 0 0
		cpu0 212383 20476 204704 8389202 7253 42 12597 0 0 0
		cpu1 224452 24947 215570 8372502 8135 4 42768 0 0 0
		cpu2 222993 17440 200925 8424262 8069 9 17732 0 0 0
		cpu3 186835 8775 195974 8486330 5746 3 8360 0 0 0
		cpu4 107075 32886 48854 8688521 3995 4 5758 0 0 0
		cpu5 90733 20914 27798 1429573 2984 1 11419 0 0 0
		intr 53261351 0 686 1 0 0 1 12 31 1 20 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 7818 0 0 0 0 0 0 0 0 255 33 1912 33 0 0 0 0 0 0 3449534 2315885 2150546 2399277 696281 339300 22642 19371 0 0 0 0 0 0 0 0 0 0 0 2199 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 2445 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 162240 14293 2858 0 151709 151592 0 0 0 284534 0 0 0 0 0 0 0 0 0 0 0 0 0 0 185353 0 0 938962 0 0 0 0 736100 0 0 1 1209 27960 0 0 0 0 0 0 0 0 303 115968 452839 2 0 0 0 0 0 0 0 0 0 0 0 0 0 160361 8835 86413 1292 0 0 0 0 0 0 0 0 0 0 0 0 0 0 3592 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 6091 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 35667 0 0 156823 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 138 2667417 0 41 4008 952 16633 533480 0 0 0 0 0 0 262506 0 0 0 0 0 0 126 0 0 1558488 0 4 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 2 2 8 0 0 6 0 0 0 10 3 4 0 0 0 0 0 3 0 0 0 0 0 0 0 0 0 0 0 20 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 12 1 1 83806 0 1 1 0 1 0 1 1 319686 2 8 0 0 0 0 0 0 0 0 0 244534 0 1 10 9 0 10 112 107 40 221 0 0 0 144
		ctxt 90182396
		btime 1595203295
		processes 270853
		procs_running 2
		procs_blocked 0
		softirq 25293348 2883 7658936 40779 539155 497187 2864 1908702 7229194 279723 7133925
		EOF
	fi

	if [ ! -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.uptime" ]; then
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.uptime"
		284684.56 513853.46
		EOF
	fi

	if [ ! -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.version" ]; then
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.version"
		Linux version 5.4.0-faked (termux@androidos) (gcc version 4.9.x (Faked /proc/version by Proot-Distro) ) #1 SMP PREEMPT Fri Jul 10 00:00:00 UTC 2020
		EOF
	fi
}

# Usage info for command_install.
command_install_help() {
	echo
	echo -e "${BYELLOW}Usage: ${BCYAN}$PROGRAM_NAME ${GREEN}install ${CYAN}[${GREEN}DISTRIBUTION ALIAS${CYAN}]${RST}"
	echo
	echo -e "${CYAN}This command will create a fresh installation of specified Linux${RST}"
	echo -e "${CYAN}distribution.${RST}"
	echo
	echo -e "${CYAN}Options:${RST}"
	echo
	echo -e "  ${GREEN}--help               ${CYAN}- Show this help information.${RST}"
	echo
	echo -e "  ${GREEN}--override-alias [new alias]   ${CYAN}- Set a custom alias for installed${RST}"
	echo -e "                                   ${CYAN}distribution.${RST}"
	echo
	echo -e "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	echo -e "${CYAN}obtained by this command: ${GREEN}$PROGRAM_NAME list${RST}"
	echo
	show_version
	echo
}

#############################################################################
#
# FUNCTION TO UNINSTALL SPECIFIED DISTRIBUTION
#
# Just deletes the rootfs of the given distribution.
#
# Accepted agruments: $1 - name of distribution.
#
command_remove() {
	local distro_name

	if [ $# -ge 1 ]; then
		case "$1" in
			-h|--help)
				command_remove_help
				return 0
				;;
			*) distro_name="$1";;
		esac
	else
		echo
		echo -e "${BRED}Error: distribution alias is not specified.${RST}"
		command_remove_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		echo
		echo -e "${BRED}Error: unknown distribution '${YELLOW}${distro_name}${BRED}' was requested to be removed.${RST}"
		echo
		echo -e "${CYAN}Use '${GREEN}${PROGRAM_NAME} list${CYAN}' to see which distributions are supported.${RST}"
		echo
		return 1
	fi

	# Not using is_distro_installed() here as we only need to know
	# whether rootfs directory is available.
	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
		echo
		echo -e "${BRED}Error: distribution '${YELLOW}${distro_name}${BRED}' is not installed.${RST}"
		echo
		return 1
	fi

	# Delete plugin with overridden alias.
	if [ "${CMD_REMOVE_REQUESTED_RESET-false}" = "false" ] && [ -e "${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh" ]; then
		echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Deleting ${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh...${RST}"
		rm -f "${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh"
	fi

	echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Wiping the rootfs of ${YELLOW}${SUPPORTED_DISTRIBUTIONS["$distro_name"]}${CYAN}...${RST}"
	# Attempt to restore permissions so directory can be removed without issues.
	chmod u+rwx -R "${INSTALLED_ROOTFS_DIR}/${distro_name}" > /dev/null 2>&1 || true
	# There is still chance for failure.
	if ! rm -rf "${INSTALLED_ROOTFS_DIR:?}/${distro_name:?}"; then
		echo -e "${BLUE}[${RED}!${BLUE}] ${CYAN}Finished with errors. Some files probably were not deleted.${RST}"
		return 1
	fi
}

# Usage info for command_remove.
command_remove_help() {
	echo
	echo -e "${BYELLOW}Usage: ${BCYAN}$PROGRAM_NAME ${GREEN}remove ${CYAN}[${GREEN}DISTRIBUTION ALIAS${CYAN}]${RST}"
	echo
	echo -e "${CYAN}This command will uninstall the specified Linux distribution.${RST}"
	echo
	echo -e "${CYAN}Be careful when using it because you will not be prompted for${RST}"
	echo -e "${CYAN}confirmation and all data saved within the distribution will${RST}"
	echo -e "${CYAN}instantly gone.${RST}"
	echo
	echo -e "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	echo -e "${CYAN}obtained by this command: ${GREEN}$PROGRAM_NAME list${RST}"
	echo
	show_version
	echo
}

#############################################################################
#
# FUNCTION TO REINSTALL THE GIVEN DISTRIBUTION
#
# Just a shortcut for command_remove && command_install.
#
# Accepted arguments: $1 - distribution name.
#
command_reset() {
	local distro_name

	if [ $# -ge 1 ]; then
		case "$1" in
			-h|--help)
				command_reset_help
				return 0
				;;
			*) distro_name="$1";;
		esac
	else
		echo
		echo -e "${BRED}Error: distribution alias is not specified.${RST}"
		command_reset_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		echo
		echo -e "${BRED}Error: unknown distribution '${YELLOW}${distro_name}${BRED}' was requested to be reinstalled.${RST}"
		echo
		echo -e "${CYAN}Use '${GREEN}${PROGRAM_NAME} list${CYAN}' to see which distributions are supported.${RST}"
		echo
		return 1
	fi

	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
		echo
		echo -e "${BRED}Error: distribution '${YELLOW}${distro_name}${BRED}' is not installed.${RST}"
		echo
		return 1
	fi

	CMD_REMOVE_REQUESTED_RESET="true" command_remove "$distro_name"
	command_install "$distro_name"
}

# Usage info for command_reset.
command_reset_help() {
	echo
	echo -e "${BYELLOW}Usage: ${BCYAN}$PROGRAM_NAME ${GREEN}reset ${CYAN}[${GREEN}DISTRIBUTION ALIAS${CYAN}]${RST}"
	echo
	echo -e "${CYAN}Reinstall the specified Linux distribution.${RST}"
	echo
	echo -e "${CYAN}Be careful when using it because you will not be prompted for${RST}"
	echo -e "${CYAN}confirmation and all data saved within the distribution will${RST}"
	echo -e "${CYAN}instantly gone.${RST}"
	echo
	echo -e "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	echo -e "${CYAN}obtained by this command: ${GREEN}$PROGRAM_NAME list${RST}"
	echo
	show_version
	echo
}

#############################################################################
#
# FUNCTION TO START SHELL OR EXECUTE COMMAND
#
# Starts root shell inside the rootfs of specified Linux distribution.
# If '--' with further arguments was specified, execute the root shell
# command and exit.
#
# Accepts arbitrary amount of arguments.
#
command_login() {
	local isolated_environment=false
	local use_termux_home=false
	local no_link2symlink=false
	local no_sysvipc=false
	local fix_low_ports=false
	local make_host_tmp_shared=false
	local distro_name=""
	local login_user="root"

	while (($# >= 1)); do
		case "$1" in
			--)
				shift 1
				break
				;;
			--help)
				command_login_help
				return 0
				;;
			--fix-low-ports)
				fix_low_ports=true
				;;
			--isolated)
				isolated_environment=true
				;;
			--no-fake-proc)
				echo -e "${BRED}Warning: option '${YELLOW}${1}${BRED}' is deprecated.${RST}"
				;;
			--termux-home)
				use_termux_home=true
				;;
			--shared-tmp)
				make_host_tmp_shared=true
				;;
			--no-link2symlink)
				no_link2symlink=true
				;;
			--no-sysvipc)
				no_sysvipc=true
				;;
			--user)
				if [ $# -ge 2 ]; then
					shift 1

					if [ -z "$1" ]; then
						echo
						echo -e "${BRED}Error: argument to option '${YELLOW}--user${BRED}' should not be empty.${RST}"
						command_login_help
						return 1
					fi

					login_user="$1"
				else
					echo
					echo -e "${BRED}Error: option '${YELLOW}$1${BRED}' requires an argument.${RST}"
					command_login_help
					return 1
				fi
				;;
			-*)
				echo
				echo -e "${BRED}Error: unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_login_help
				return 1
				;;
			*)
				if [ -z "$1" ]; then
					echo
					echo -e "${BRED}Error: you should not pass empty command line arguments.${RST}"
					command_login_help
					return 1
				fi

				if [ -z "$distro_name" ]; then
					distro_name="$1"
				else
					echo
					echo -e "${BRED}Error: unknown option '${YELLOW}${1}${BRED}'.${RST}"
					echo
					echo -e "${BRED}Error: you have already set distribution as '${YELLOW}${distro_name}${BRED}'.${RST}"
					command_login_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -z "$distro_name" ]; then
		echo
		echo -e "${BRED}Error: you should at least specify a distribution in order to log in.${RST}"
		command_login_help
		return 1
	fi

	if is_distro_installed "$distro_name"; then
		if [ -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/.l2s" ]; then
			export PROOT_L2S_DIR="${INSTALLED_ROOTFS_DIR}/${distro_name}/.l2s"
		fi
		unset LD_PRELOAD
		#export PROOT_NO_SECCOMP=1

		if [ $# -ge 1 ]; then
			# Wrap in quotes each argument to prevent word splitting.
			local -a shell_command_args
			for i in "$@"; do
				shell_command_args+=("'$i'")
			done

			set -- "/bin/su" "-l" "$login_user" "-c" "${shell_command_args[*]}"
		else
			set -- "/bin/su" "-l" "$login_user"
		fi

		# Setup the default environment as well as copy some variables
		# defined by Termux. Note that when copying variables, we don't
		# care whether they actually defined in Termux or not. If they
		# will be empty, this should not cause any issues.
		set -- "/usr/bin/env" "-i" \
			"HOME=/root" \
			"LANG=C.UTF-8" \
			"TERM=${TERM-xterm-256color}" \
			"$@"

		set -- "--rootfs=${INSTALLED_ROOTFS_DIR}/${distro_name}" "$@"

		# Terminate all processes on exit so proot won't hang.
		set -- "--kill-on-exit" "$@"

		if ! $no_link2symlink; then
			# Support hardlinks.
			set -- "--link2symlink" "$@"
		fi

		if ! $no_sysvipc; then
			# Support System V IPC.
			set -- "--sysvipc" "$@"
		fi

		# Some devices have old kernels and GNU libc refuses to work on them.
		# Fix this behavior by reporting a fake up-to-date kernel version.
		set -- "--kernel-release=5.4.0-faked" "$@"

		# Simulate root so we can switch users.
		set -- "--cwd=/root" "$@"
		set -- "--root-id" "$@"

		# Core file systems that should always be present.
		set -- "--bind=/dev" "$@"
		set -- "--bind=/dev/urandom:/dev/random" "$@"
		set -- "--bind=/proc" "$@"
		set -- "--bind=/proc/self/fd:/dev/fd" "$@"
		set -- "--bind=/proc/self/fd/0:/dev/stdin" "$@"
		set -- "--bind=/proc/self/fd/1:/dev/stdout" "$@"
		set -- "--bind=/proc/self/fd/2:/dev/stderr" "$@"
		set -- "--bind=/sys" "$@"

		# Ensure that we can bind fake /proc entries.
		setup_fake_proc

		# Fake /proc/loadavg if necessary.
		if ! cat /proc/loadavg > /dev/null 2>&1; then
			set -- "--bind=${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.loadavg:/proc/loadavg" "$@"
		fi

		# Fake /proc/stat if necessary.
		if ! cat /proc/stat > /dev/null 2>&1; then
			set -- "--bind=${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.stat:/proc/stat" "$@"
		fi

		# Fake /proc/uptime if necessary.
		if ! cat /proc/uptime > /dev/null 2>&1; then
			set -- "--bind=${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.uptime:/proc/uptime" "$@"
		fi

		# Fake /proc/version if necessary.
		if ! cat /proc/version > /dev/null 2>&1; then
			set -- "--bind=${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.version:/proc/version" "$@"
		fi

		# Bind /tmp to /dev/shm.
		if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp" ]; then
			mkdir -p "${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp"
		fi
		set -- "--bind=${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp:/dev/shm" "$@"

		# When running in non-isolated mode, provide some bindings specific
		# to Android and Termux so user can interact with host file system.
		if ! $isolated_environment; then
			if [ -d "/apex" ]; then
				set -- "--bind=/apex" "$@"
			fi
			set -- "--bind=/data/dalvik-cache" "$@"
			set -- "--bind=/data/data/com.termux" "$@"
			set -- "--bind=/storage" "$@"
			set -- "--bind=/storage/self/primary:/sdcard" "$@"
			set -- "--bind=/system" "$@"
			set -- "--bind=/vendor" "$@"
			if [ -f "/plat_property_contexts" ]; then
				set -- "--bind=/plat_property_contexts" "$@"
			fi
			if [ -f "/property_contexts" ]; then
				set -- "--bind=/property_contexts" "$@"
			fi
		fi

		# Use Termux home directory if requested.
		# Ignores --isolated.
		if $use_termux_home; then
			if [ "$login_user" = "root" ]; then
				set -- "--bind=@TERMUX_HOME@:/root" "$@"
			else
				set -- "--bind=@TERMUX_HOME@:/home/${login_user}" "$@"
			fi
		fi

		# Bind the tmp folder from the host system to the guest system
		# Ignores --isolated.
		if $make_host_tmp_shared; then
			set -- "--bind=@TERMUX_PREFIX@/tmp:/tmp" "$@"
		fi

		# Modify bindings to protected ports to use a higher port number.
		if $fix_low_ports; then
			set -- "-p" "$@"
		fi

		exec proot "$@"
	else
		if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
			echo
			echo -e "${BRED}Error: cannot log in into unknown distribution '${YELLOW}${distro_name}${BRED}'.${RST}"
			echo
			echo -e "${CYAN}Use '${GREEN}${PROGRAM_NAME} list${CYAN}' to see which distributions are supported.${RST}"
			echo
		else
			echo
			echo -e "${BRED}Error: distribution '${YELLOW}${distro_name}${BRED}' is not installed.${RST}"
			echo
			echo -e "${CYAN}Install it with: ${GREEN}${PROGRAM_NAME} install ${distro_name}${RST}"
			echo
		fi
		return 1
	fi
}

# Usage info for command_login.
command_login_help() {
	echo
	echo -e "${BYELLOW}Usage: ${BCYAN}$PROGRAM_NAME ${GREEN}login ${CYAN}[${GREEN}OPTIONS${CYAN}] [${GREEN}DISTRO ALIAS${CYAN}] [${GREEN}--${CYAN}[${GREEN}COMMAND${CYAN}]]${RST}"
	echo
	echo -e "${CYAN}This command will launch a login shell for the specified${RST}"
	echo -e "${CYAN}distribution if no additional arguments were given, otherwise${RST}"
	echo -e "${CYAN}it will execute the given command and exit.${RST}"
	echo
	echo -e "${CYAN}Options:${RST}"
	echo
	echo -e "  ${GREEN}--help               ${CYAN}- Show this help information.${RST}"
	echo
	echo -e "  ${GREEN}--user [user]        ${CYAN}- Login as specified user instead of 'root'.${RST}"
	echo
	echo -e "  ${GREEN}--fix-low-ports      ${CYAN}- Modify bindings to protected ports to use${RST}"
	echo -e "                         ${CYAN}a higher port number.${RST}"
	echo
	echo -e "  ${GREEN}--isolated           ${CYAN}- Run isolated environment without access${RST}"
	echo -e "                         ${CYAN}to host file system.${RST}"
	echo
	echo -e "  ${GREEN}--termux-home        ${CYAN}- Mount Termux home directory to /root.${RST}"
	echo -e "                         ${CYAN}Takes priority over '${GREEN}--isolated${CYAN}' option.${RST}"
	echo
	echo -e "  ${GREEN}--shared-tmp         ${CYAN}- Mount Termux temp directory to /tmp.${RST}"
	echo -e "                         ${CYAN}Takes priority over '${GREEN}--isolated${CYAN}' option.${RST}"
	echo
	echo -e "  ${GREEN}--no-link2symlink    ${CYAN}- Disable hardlink emulation by proot.${RST}"
	echo -e "                         ${CYAN}Adviseable only on devices with SELinux${RST}"
	echo -e "                         ${CYAN}in permissive mode.${RST}"
	echo
	echo -e "  ${GREEN}--no-sysvipc         ${CYAN}- Disable System V IPC emulation by proot.${RST}"
	echo
	echo -e "${CYAN}Put '${GREEN}--${CYAN}' if you wish to stop command line processing and pass${RST}"
	echo -e "${CYAN}options as shell arguments.${RST}"
	echo
	echo -e "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	echo -e "${CYAN}obtained by this command: ${GREEN}$PROGRAM_NAME list${RST}"
	echo
	echo -e "${CYAN}If no '${GREEN}--isolated${CYAN}' option given, the following host directories${RST}"
	echo -e "${CYAN}will be available:${RST}"
	echo
	echo -e "  ${CYAN}* ${YELLOW}/apex ${CYAN}(only Android 10+)${RST}"
	echo -e "  ${CYAN}* ${YELLOW}/data/dalvik-cache${RST}"
	echo -e "  ${CYAN}* ${YELLOW}/data/data/com.termux${RST}"
	echo -e "  ${CYAN}* ${YELLOW}/sdcard${RST}"
	echo -e "  ${CYAN}* ${YELLOW}/storage${RST}"
	echo -e "  ${CYAN}* ${YELLOW}/system${RST}"
	echo -e "  ${CYAN}* ${YELLOW}/vendor${RST}"
	echo
	echo -e "${CYAN}This should be enough to get Termux utilities like termux-api or${RST}"
	echo -e "${CYAN}termux-open get working. If they do not work for some reason,${RST}"
	echo -e "${CYAN}check if these files are sourced by your shell:${RST}"
	echo
	echo -e "  ${CYAN}* ${YELLOW}/etc/profile.d/termux-proot.sh${RST}"
	echo -e "  ${CYAN}* ${YELLOW}/etc/profile${RST}"
	echo
	echo -e "${CYAN}Also check whether they define variables like ANDROID_DATA,${RST}"
	echo -e "${CYAN}ANDROID_ROOT, BOOTCLASSPATH and others which are usually set${RST}"
	echo -e "${CYAN}in Termux sessions.${RST}"
	echo
	show_version
	echo
}

#############################################################################
#
# FUNCTION TO LIST THE SUPPORTED DISTRIBUTIONS
#
# Shows the list of distributions which this utility can handle. Also print
# their installation status.
#
command_list() {
	echo
	if [ -z "${!SUPPORTED_DISTRIBUTIONS[*]}" ]; then
		echo -e "${YELLOW}You do not have any distribution plugins configured.${RST}"
		echo
		echo -e "${YELLOW}Please check the directory '$DISTRO_PLUGINS_DIR'.${RST}"
	else
		echo -e "${CYAN}Supported distributions:${RST}"

		local i
		for i in $(echo "${!SUPPORTED_DISTRIBUTIONS[@]}" | tr ' ' '\n' | sort -d); do
			echo
			echo -e "  ${CYAN}* ${YELLOW}${SUPPORTED_DISTRIBUTIONS[$i]}${RST}"
			echo
			echo -e "    ${CYAN}Alias: ${YELLOW}${i}${RST}"
			if is_distro_installed "$i"; then
				echo -e "    ${CYAN}Status: ${GREEN}installed${RST}"
			else
				echo -e "    ${CYAN}Status: ${RED}NOT installed${RST}"
			fi
			if [ -n "${SUPPORTED_DISTRIBUTIONS_COMMENTS["${i}"]+x}" ]; then
				echo -e "    ${CYAN}Comment: ${SUPPORTED_DISTRIBUTIONS_COMMENTS["${i}"]}${RST}"
			fi
		done

		echo
		echo -e "${CYAN}Install selected one with: ${GREEN}${PROGRAM_NAME} install <alias>${RST}"
	fi
	echo
}

#############################################################################
#
# FUNCTION TO PRINT UTILITY USAGE INFORMATION
#
# Prints a basic overview of the available commands and list of supported
# distributions.
#
command_help() {
	echo
	echo -e "${BYELLOW}Usage: ${BCYAN}$PROGRAM_NAME${CYAN} [${GREEN}COMMAND${CYAN}] [${GREEN}ARGUMENTS${CYAN}]${RST}"
	echo
	echo -e "${CYAN}Utility to manage proot'ed Linux distributions inside Termux.${RST}"
	echo
	echo -e "${CYAN}List of the available commands:${RST}"
	echo
	echo -e "  ${GREEN}help     ${CYAN}- Show this help information.${RST}"
	echo
	echo -e "  ${GREEN}install  ${CYAN}- Install a specified distribution.${RST}"
	echo
	echo -e "  ${GREEN}list     ${CYAN}- List supported distributions and their installation${RST}"
	echo -e "             ${CYAN}status.${RST}"
	echo
	echo -e "  ${GREEN}login    ${CYAN}- Start login shell for the specified distribution.${RST}"
	echo
	echo -e "  ${GREEN}remove   ${CYAN}- Delete a specified distribution.${RST}"
	echo -e "             ${RED}WARNING: this command destroys data!${RST}"
	echo
	echo -e "  ${GREEN}reset    ${CYAN}- Reinstall from scratch a specified distribution.${RST}"
	echo -e "             ${RED}WARNING: this command destroys data!${RST}"
	echo
	echo -e "${CYAN}Each of commands has its own help information. To view it, just${RST}"
	echo -e "${CYAN}supply a '${GREEN}--help${CYAN}' argument to chosen command.${RST}"
	echo
	echo -e "${CYAN}Hint: type command '${GREEN}${PROGRAM_NAME} list${CYAN}' to get a list of the${RST}"
	echo -e "${CYAN}supported distributions. Pick a distro alias and run the next${RST}"
	echo -e "${CYAN}command to install it: ${GREEN}${PROGRAM_NAME} install <alias>${RST}"
	echo
	echo -e "${CYAN}Runtime data is stored at this location:${RST}"
	echo -e "${CYAN}${RUNTIME_DIR}${RST}"
	echo
	echo -e "${CYAN}If you have issues with proot during installation or login, try${RST}"
	echo -e "${CYAN}to set '${GREEN}PROOT_NO_SECCOMP=1${CYAN}' environment variable.${RST}"
	echo
	show_version
	echo
}

#############################################################################
#
# FUNCTION TO PRINT VERSION STRING
#
# Prints version & author information. Used in functions for displaying
# usage info.
#
show_version() {
	echo -e "${ICYAN}Proot-Distro v${PROGRAM_VERSION} by @xeffyr.${RST}"
}

#############################################################################
#
# ENTRY POINT
#
# 1. Check for dependencies. Assume that coreutils, findutils, tar, bzip2,
#    gzip, xz are always available.
# 2. Check all available distribution plug-ins.
# 3. Handle the requested commands or show help when '-h/--help/help' were
#    given. Further command line processing is offloaded to requested command.
#

# This will be executed when signal HUP/INT/TERM is received.
trap 'echo -e "\\r${BLUE}[${RED}!${BLUE}] ${CYAN}Exiting immediately as requested.${RST}"; exit 1;' HUP INT TERM

for i in curl proot; do
	if [ -z "$(command -v "$i")" ]; then
		echo
		echo -e "${BRED}Utility '${i}' is not installed. Cannot continue.${RST}"
		echo
		exit 1
	fi
done
unset i

declare -A SUPPORTED_DISTRIBUTIONS
declare -A SUPPORTED_DISTRIBUTIONS_COMMENTS
while read -r filename; do
	distro_name=$(. "$filename"; echo "${DISTRO_NAME-}")
	distro_comment=$(. "$filename"; echo "${DISTRO_COMMENT-}")
	# May have 2 name formats:
	# * alias.override.sh
	# * alias.sh
	# but we need to treat both as 'alias'.
	distro_alias=${filename%%.override.sh}
	distro_alias=${distro_alias%%.sh}
	distro_alias=$(basename "$distro_alias")

	# We getting distribution name from $DISTRO_NAME which
	# should be set in plug-in.
	if [ -z "$distro_name" ]; then
		echo
		echo -e "${BRED}Error: no DISTRO_NAME defined in '${YELLOW}${filename}${BRED}'.${RST}"
		echo
		exit 1
	fi

	SUPPORTED_DISTRIBUTIONS["$distro_alias"]="$distro_name"
	[ -n "$distro_comment" ] && SUPPORTED_DISTRIBUTIONS_COMMENTS["$distro_alias"]="$distro_comment"
done < <(find "$DISTRO_PLUGINS_DIR" -maxdepth 1 -type f -iname "*.sh" 2>/dev/null)
unset distro_name distro_alias

if [ $# -ge 1 ]; then
	case "$1" in
		-h|--help|help) shift 1; command_help;;
		install) shift 1; command_install "$@";;
		remove) shift 1; CMD_REMOVE_REQUESTED_RESET="false" command_remove "$@";;
		reset) shift 1; command_reset "$@";;
		login) shift 1; command_login "$@";;
		list) shift 1; command_list;;
		*)
			echo
			echo -e "${BRED}Error: unknown command '${YELLOW}$1${BRED}'.${RST}"
			echo
			echo -e "${CYAN}Run '${GREEN}${PROGRAM_NAME} help${CYAN}' to see the list of available commands.${RST}"
			echo
			exit 1
			;;
	esac
else
	echo
	echo -e "${BRED}Error: no command provided.${RST}"
	command_help
fi

exit 0
