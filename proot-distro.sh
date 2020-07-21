#!/usr/bin/env bash
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

set -e -u

PROGRAM_VERSION="0.1"

PROGRAM_NAME=$(basename "$(realpath "$0")")
DISTRO_PLUGINS_DIR="@TERMUX_PREFIX@/etc/proot-distro"
UTILITY_BASEDIR="@TERMUX_PREFIX@/var/lib/proot-distro"
DOWNLOADED_ROOTFS_DIR="${UTILITY_BASEDIR}/dlcache"
INSTALLED_ROOTFS_DIR="${UTILITY_BASEDIR}/installed-rootfs"

#############################################################################
#
# ANTI-ROOT FUSE
#
# This script should never be executed as root as can mess up the ownership,
# and SELinux labels in $PREFIX.
#
if [ "$(id -u)" = "0" ]; then
	echo
	echo "${PROGRAM_NAME}: I have detected a root user id and cannot continue the execution. Running this script as root may be dangerous."
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
# FUNCTION TO PREPARE PROOT FOR USE
#
# Checks whether proot is installed. Creates a per-distribution directory
# for storing data created by link2symlink proot extension.
#
# Accepted arguments: $1 - name of distribution.
#
setup_proot() {
	if [ -z "$(command -v proot)" ]; then
		echo
		echo "Utility 'proot' is not installed. Cannot continue."
		echo
		return 1
	fi

	export PROOT_L2S_DIR="${INSTALLED_ROOTFS_DIR}/${1}/.l2s"
	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${1}/.l2s" ]; then
		echo "[*] Creating directory '$PROOT_L2S_DIR'..."
		mkdir -p "$PROOT_L2S_DIR"
	fi

	# We need this to disable the preloaded libtermux-exec.so library
	# which redefines 'execve()' implementation.
	unset LD_PRELOAD
}

#############################################################################
#
# FUNCTION TO LIST THE SUPPORTED DISTRIBUTIONS
#
# Shows the list of distributions which this utility can handle. Also print
# their installation status.
#
show_supported_distributions() {
	echo "Supported distributions:"
	echo
	local i
	for i in "${!SUPPORTED_DISTRIBUTIONS[@]}"; do
		if is_distro_installed "$i"; then
			echo "  * ${SUPPORTED_DISTRIBUTIONS[$i]} (alias: $i, status: installed)"
		else
			echo "  * ${SUPPORTED_DISTRIBUTIONS[$i]} (alias: $i, status: NOT installed)"
		fi
	done | sort -d
}

#############################################################################
#
# FUNCTION TO INSTALL THE SPECIFIED DISTRIBUTION
#
# Installs the Linux distribution by the following algorithm:
#
#  1. Checks whether requested distribution is supported, if yes - continue.
#  2. Checks whether requested distribution is installed, if not - continue.
#  3. Configure environment and check whether proot is installed.
#  4. Source the distribution configuration plug-in which contains the
#     functionality necessary for installation. It must define at least
#     get_download_url() function which returns a download URL.
#  5. Download the rootfs archive, if it is not available in cache.
#  6. Extract the rootfs by using `tar` running under proot with link2symlink
#     extension.
#  7. Add missing Android specific UIDs/GIDs to user database.
#  8. Execute optional setup hook (distro_setup) if present.
#
# Accepted arguments: $1 - distribution name.
#
command_install() {
	local distro_name

	if [ $# -ge 1 ]; then
		case "$1" in
			-h|--help)
				command_install_help
				return 0
				;;
			*) distro_name="$1";;
		esac
	else
		echo
		echo "Error: distribution name is not specified."
		command_install_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		echo
		echo "Error: unknown distribution '$distro_name' was requested to be installed."
		echo
		show_supported_distributions
		echo
		echo "Note that distributions should be referenced by alias when supplied to command line."
		echo
		return 1
	fi

	if is_distro_installed "$distro_name"; then
		echo
		echo "Error: distribution '$distro_name' is already installed."
		echo
		echo "Log in:     $PROGRAM_NAME login $distro_name"
		echo "Reinstall:  $PROGRAM_NAME reset $distro_name"
		echo "Uninstall:  $PROGRAM_NAME remove $distro_name"
		echo
		return 1
	fi

	if [ ! -d "$INSTALLED_ROOTFS_DIR" ]; then
		echo "[*] Creating directory '$INSTALLED_ROOTFS_DIR'..."
		mkdir -p "$INSTALLED_ROOTFS_DIR"
	fi

	setup_proot "$distro_name"

	if [ -f "${DISTRO_PLUGINS_DIR}/${distro_name}.sh" ]; then
		echo "[*] Installing ${SUPPORTED_DISTRIBUTIONS["$distro_name"]}..."

		# Some distributions store rootfs in subdirectory - in this case
		# this variable should be set to 1.
		DISTRO_TARBALL_STRIP_OPT=0

		# Distribution plug-in contains steps on how to get download URL
		# and further post-installation configuration.
		source "${DISTRO_PLUGINS_DIR}/${distro_name}.sh"

		local download_url
		if declare -f -F get_download_url >/dev/null 2>&1; then
			download_url=$(get_download_url)
		else
			echo
			echo "Error: get_download_url() is not defined in ${DISTRO_PLUGINS_DIR}/${distro_name}.sh"
			echo
			return 1
		fi

		if [ -z "$download_url" ]; then
			echo "[!] Sorry, but distribution download URL is not defined for your CPU architecture '$(uname -m)'."
			return 1
		fi

		if [ ! -d "$DOWNLOADED_ROOTFS_DIR" ]; then
			echo "[*] Creating directory '$DOWNLOADED_ROOTFS_DIR'..."
			mkdir -p "$DOWNLOADED_ROOTFS_DIR"
		fi

		local tarball_name
		tarball_name=$(basename "$download_url")

		if [ ! -f "${DOWNLOADED_ROOTFS_DIR}/${tarball_name}" ]; then
			echo "[*] Downloading rootfs tarball for '$distro_name'..."

			# Using temporary file as script can't distinguish the partially
			# downloaded file from the complete. Useful in case if curl will
			# fail for some reason.
			echo
			rm -f "${DOWNLOADED_ROOTFS_DIR}/${tarball_name}.tmp"
			if ! curl --fail --retry 5 --retry-connrefused --retry-delay 5 --location \
				--output "${DOWNLOADED_ROOTFS_DIR}/${tarball_name}.tmp" "$download_url"; then
				echo "[!] Download failure, please check your network connection."
				rm -f "${DOWNLOADED_ROOTFS_DIR}/${tarball_name}.tmp"
				return 1
			fi
			echo

			# If curl finished successfully, rename file to original.
			mv -f "${DOWNLOADED_ROOTFS_DIR}/${tarball_name}.tmp" "${DOWNLOADED_ROOTFS_DIR}/${tarball_name}"
		else
			echo "[*] Using cached rootfs tarball for '$distro_name'..."
		fi

		if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
			echo "[*] Creating directory '${INSTALLED_ROOTFS_DIR}/${distro_name}'..."
			mkdir -p "${INSTALLED_ROOTFS_DIR}/${distro_name}"
		fi

		echo "[*] Extracting rootfs, please wait..."
		# --exclude='dev'||: - need to exclude /dev directory which may contain device files.
		# --delay-directory-restore - set directory permissions only when files were extracted
		#                             to avoid issues with Arch Linux bootstrap archives.
		proot --link2symlink \
			tar -C "${INSTALLED_ROOTFS_DIR}/${distro_name}" --warning=no-unknown-keyword \
			--delay-directory-restore --strip="$DISTRO_TARBALL_STRIP_OPT" \
			-xf "${DOWNLOADED_ROOTFS_DIR}/${tarball_name}" --exclude='dev'||:

		# Write important environment variables to profile file as /bin/login does not
		# preserve them.
		local profile_script
		if [ -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile.d" ]; then
			profile_script="${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile.d/termux-proot.sh"
		else
			profile_script="${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile"
		fi
		echo "[*] Writing '$profile_script'..."
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
		export PATH=\${PATH}:/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin
		export PREFIX=${PREFIX-/data/data/com.termux/files/usr}
		export TERM=${TERM-xterm-256color}
		export TMPDIR=/tmp
		EOF

		# /etc/resolv.conf may not be configured, so write in it our configuraton.
		echo "[*] Creating DNS resolver configuration (NS 1.1.1.1/1.0.0.1)..."
		rm -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/resolv.conf"
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/resolv.conf"
		nameserver 1.1.1.1
		nameserver 1.0.0.1
		EOF

		# Add Android-specific UIDs/GIDs to /etc/group and /etc/gshadow.
		echo "[*] Registering Android-specific UIDs and GIDs..."
		echo "aid_$(id -un):x:$(id -u):$(id -g):Android user:/:/usr/sbin/nologin" >> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/passwd"
		echo "aid_$(id -un):*:18446:0:99999:7:::" >> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/shadow"
		local g
		for g in $(id -G); do
			echo "aid_$(id -gn "$g"):x:${g}:root,aid_$(id -un)" >> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/group"
			if [ -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/gshadow" ]; then
				echo "aid_$(id -gn "$g"):*::root,aid_$(id -un)" >> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/gshadow"
			fi
		done

		# Run optional distro-specific hook.
		if declare -f -F distro_setup >/dev/null 2>&1; then
			echo "[*] Running distro-specific configuration steps..."
			(cd "${INSTALLED_ROOTFS_DIR}/${distro_name}"
				distro_setup
			)
		fi

		echo "[*] Installation finished."
		echo
		echo "Now run '$PROGRAM_NAME login $distro_name' to log in."
		echo
		return 0
	else
		echo "[!] Cannot find '${DISTRO_PLUGINS_DIR}/${distro_name}.sh' which contains distro-specific install functions."
		return 1
	fi
}

# Special function for executing a command in rootfs.
# Can be used only inside distro_setup().
run_proot_cmd() {
	if [ -z "${distro_name-}" ]; then
		echo
		echo "Error: called run_proot_cmd() but \$distro_name is not set."
		echo "Possible cause: using run_proot_cmd() outside of distro_setup() ?"
		echo
		return 1
	fi

	proot --rootfs="${INSTALLED_ROOTFS_DIR}/${distro_name}" --link2symlink \
		--kill-on-exit --root-id --cwd=/root --bind=/dev --bind=/proc --bind=/sys \
		/usr/bin/env -i \
			"HOME=/root" \
			"LANG=C.UTF-8" \
			"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
			"TERM=$TERM" \
			"TMPDIR=/tmp" \
			"$@"
}

# Usage info for command_install.
command_install_help() {
	echo
	echo "Usage: $PROGRAM_NAME install [DISTRIBUTION ALIAS]"
	echo
	echo "This command will create a fresh installation of specified Linux distribution."
	echo
	show_supported_distributions
	echo
	echo "Selected distribution should be referenced by alias."
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
		echo "Error: distribution name is not specified."
		command_remove_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		echo
		echo "Error: unknown distribution '$distro_name' was requested to be removed."
		echo
		show_supported_distributions
		echo
		echo "Note that distributions should be referenced by alias when supplied to command line."
		echo
		return 1
	fi

	# Not using is_distro_installed() here as we only need to know
	# whether rootfs directory is available.
	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
		echo
		echo "Error: distribution '$distro_name' is not installed."
		command_remove_help
		return 1
	fi

	echo "[*] Deleting '${INSTALLED_ROOTFS_DIR}/${distro_name}'..."
	# Attempt to restore permissions so directory can be removed without issues.
	chmod u+rwx -R "${INSTALLED_ROOTFS_DIR}/${distro_name}" > /dev/null 2>&1 || true
	# There is still chance for failure.
	if ! rm -rf "${INSTALLED_ROOTFS_DIR:?}/${distro_name:?}"; then
		echo "[*] Finished with errors. Some files probably were not deleted."
		return 1
	fi
}

# Usage info for command_remove.
command_remove_help() {
	echo
	echo "Usage: $PROGRAM_NAME remove [DISTRIBUTION ALIAS]"
	echo
	echo "This command will uninstall the specified Linux distribution."
	echo
	show_supported_distributions
	echo
	echo "Selected distribution should be referenced by alias."
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
		echo "Error: distribution name is not specified."
		command_reset_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		echo
		echo "Error: unknown distribution '$distro_name' was requested to be reinstalled."
		echo
		show_supported_distributions
		echo
		echo "Note that distributions should be referenced by alias when supplied to command line."
		echo
		return 1
	fi

	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
		echo
		echo "Error: distribution '$distro_name' is not installed."
		command_reset_help
		return 1
	fi

	command_remove "$distro_name"
	command_install "$distro_name"
}

# Usage info for command_reset.
command_reset_help() {
	echo
	echo "Usage: $PROGRAM_NAME reset [DISTRIBUTION ALIAS]"
	echo
	echo "Reinstall the specified Linux distribution."
	echo
	show_supported_distributions
	echo
	echo "Selected distribution should be referenced by alias."
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
# Accepts arbitrary amount of arguments. When '--' was given, stops the
# further command line processing.
#
command_login() {
	local isolated_environment=false
	local distro_name=""

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
			--isolated)
				isolated_environment=true
				;;
			-*)
				echo
				echo "Unknown option '$1'."
				command_login_help
				return 1
				;;
			*)
				if [ -z "$1" ]; then
					echo
					echo "Error: you should not pass empty command line arguments."
					command_login_help
					return 1
				fi

				if [ -z "$distro_name" ]; then
					distro_name="$1"
				else
					echo
					echo "Unknown option '$1'. You have already set distribution as '$distro_name'."
					command_login_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -z "$distro_name" ]; then
		echo
		echo "Error: you should at least specify a distribution in order to log in."
		command_login_help
		return 1
	fi

	if is_distro_installed "$distro_name"; then
		setup_proot "$distro_name"

		if [ $# -ge 1 ]; then
			# Wrap in quotes each argument to prevent word splitting.
			local -a shell_command_args
			for i in "$@"; do
				shell_command_args+=("\"$i\"")
			done

			set -- "/bin/su" "-l" "-c" "${shell_command_args[*]}"
		else
			set -- "/bin/su" "-l"
		fi

		# Setup the default environment as well as copy some variables
		# defined by Termux. Note that when copying variables, we don't
		# care whether they actually defined in Termux or not. If they
		# will be empty, this should not cause any issues.
		set -- "/usr/bin/env" "-i" \
			"ANDROID_ART_ROOT=${ANDROID_ART_ROOT-}" \
			"ANDROID_DATA=${ANDROID_DATA-}" \
			"ANDROID_I18N_ROOT=${ANDROID_I18N_ROOT-}" \
			"ANDROID_ROOT=${ANDROID_ROOT-}" \
			"ANDROID_RUNTIME_ROOT=${ANDROID_RUNTIME_ROOT-}" \
			"ANDROID_TZDATA_ROOT=${ANDROID_TZDATA_ROOT-}" \
			"BOOTCLASSPATH=${BOOTCLASSPATH-}" \
			"COLORTERM=${COLORTERM-}" \
			"DEX2OATBOOTCLASSPATH=${DEX2OATBOOTCLASSPATH-}" \
			"EXTERNAL_STORAGE=${EXTERNAL_STORAGE-}" \
			"HOME=/root" \
			"LANG=C.UTF-8" \
			"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/data/data/com.termux/files/usr/bin:/system/bin:/system/xbin" \
			"PREFIX=${PREFIX-/data/data/com.termux/files/usr}" \
			"TERM=${TERM-xterm-256color}" \
			"$@"

		set -- "--rootfs=${INSTALLED_ROOTFS_DIR}/${distro_name}" "$@"

		# Terminate all processes on exit so proot won't hang.
		set -- "--kill-on-exit" "$@"

		# Support hardlinks.
		set -- "--link2symlink" "$@"

		# Simulate root so we can switch users.
		set -- "--cwd=/root" "$@"
		set -- "--root-id" "$@"

		# Core file systems that should always be present.
		set -- "--bind=/dev" "$@"
		set -- "--bind=/proc" "$@"
		set -- "--bind=/sys" "$@"

		# Bind /tmp to /dev/shm.
		if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp" ]; then
			mkdir -p "${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp"
		fi
		set -- "--bind=${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp:/dev/shm" "$@"

		# When running in non-isolated mode, provide some bindings specific
		# to Android and Termux so user can interact with host file system.
		if ! $isolated_environment; then
			set -- "--bind=/data/dalvik-cache" "$@"
			set -- "--bind=/data/data/com.termux" "$@"
			set -- "--bind=/storage" "$@"
			set -- "--bind=/storage/emulated/0:/sdcard" "$@"
			set -- "--bind=/system" "$@"
			set -- "--bind=/vendor" "$@"
			if [ -f "/plat_property_contexts" ]; then
				set -- "--bind=/plat_property_contexts" "$@"
			fi
		fi

		exec proot "$@"
	else
		echo
		echo "Error: distribution '$distro_name' is not installed."
		echo
		show_supported_distributions
		echo
		echo "You can install the chosen distribution by '$PROGRAM_NAME install <alias>'."
		echo
		return 1
	fi
}

# Usage info for command_login.
command_login_help() {
	echo
	echo "Usage: $PROGRAM_NAME login [OPTIONS] [DISTRO ALIAS] [--[COMMAND]]"
	echo
	echo "This command will launch a login shell for the specified distribution if no additional arguments were given, otherwise it will execute the given command and exit."
	echo
	echo "Options:"
	echo
	echo "  --isolated  - Run isolated environment without access to host file system."
	echo
	echo "Put '--' if you wish to stop command line processing and pass options as shell arguments."
	echo
	show_supported_distributions
	echo
}

#############################################################################
#
# FUNCTION TO PRINT UTILITY USAGE INFORMATION
#
# Prints a basic overview of the available commands, list of supported
# distributions and version.
#
command_help() {
	echo
	echo "Usage: $PROGRAM_NAME [COMMAND] [ARGUMENTS]"
	echo
	echo "Utility to manage proot'ed Linux distributions inside Termux."
	echo
	echo "List of the available commands:"
	echo
	echo "  install  - install a specified distribution."
	echo "  list     - list supported distributions and their installation status."
	echo "  login    - start login shell for the specified distribution."
	echo "  remove   - delete a specified distribution."
	echo "  reset    - reinstall from scratch a specified distribution."
	echo
	echo "Each of commands has its own help information. To view it, just supply"
	echo "a '--help' argument to chosen command."
	echo
	show_supported_distributions
	echo
	echo "Proot-Distro version $PROGRAM_VERSION by @xeffyr."
	echo
}

#############################################################################
#
# ENTRY POINT
#
# 1. Check all available distribution plug-ins.
# 2. Handle the requested commands or show help when '-h/--help/help' were
#    given. Further command line processing is offloaded to requested command.
#
declare -A SUPPORTED_DISTRIBUTIONS
while read -r filename; do
	distro_name=$(. "$filename"; echo "${DISTRO_NAME-}")
	distro_alias=${filename%%.sh}
	distro_alias=$(basename "$distro_alias")

	# We getting distribution name from $DISTRO_NAME which
	# should be set in plug-in.
	if [ -z "$distro_name" ]; then
		echo
		echo "Error: no DISTRO_NAME defined in '$filename'."
		echo
		exit 1
	fi

	SUPPORTED_DISTRIBUTIONS["$distro_alias"]="$distro_name"
done < <(find "$DISTRO_PLUGINS_DIR" -maxdepth 1 -type f -iname "*.sh")
unset distro_name distro_alias

if [ $# -ge 1 ]; then
	case "$1" in
		-h|--help|help) shift 1; command_help;;
		install) shift 1; command_install "$@";;
		remove) shift 1; command_remove "$@";;
		reset) shift 1; command_reset "$@";;
		login) shift 1; command_login "$@";;
		list) shift 1; echo; show_supported_distributions; echo;;
		*)
			echo
			echo "Unknown command '$1'."
			echo "Run '$PROGRAM_NAME help' to see the list of available commands."
			echo
			exit 1
			;;
	esac
else
	command_help
fi

exit 0
