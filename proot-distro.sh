#!@TERMUX_PREFIX@/bin/bash
##
## Script for managing PRoot containers with Linux distributions.
##
## Originally created by Leonid Pliushch <sylirre@termux.dev> for Termux
## project.
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

PROGRAM_VERSION="3.13.0"

#############################################################################
##
## >>> CONTRIBUTOR'S NOTICE <<<
##
## By contributing to the PRoot-Distro project you are obligated to follow
## the next mandatory rules:
##
## 1. The PRoot-Distro project is built around PRoot and is intended to be
##    developed as a wrapper around PRoot. Supporting classical Linux chroot
##    is outside of the project scope.
##
## 2. The PRoot-Distro is designed to work only in Termux. Supporting other
##    distributions where PRoot is available is out of scope.
##
## 3. The PRoot-Distro is intended to be used by a non-root user. Using it
##    as superuser (root) is possible but has a potential of messing up
##    ownership of Termux environment directories and in some cases it also
##    may wipe Android device data completely.
##
##    The measures such as checking current user id must be used to prevent
##    using PRoot-Distro by the root user.
##
## 4. PRoot-Distro is not a generic tool. Its idea is to provide user a
##    high level intrument for managing Linux distributions for PRoot and
##    hide the all underlying routines and automate everything that is
##    possible. It is not all-in-one swiss-knife framework for PRoot.
##    Therefore PRoot-Distro does not provide a way to modify its behavior.
##
## ***
##
## 5. Follow the general design.
##
##    Proot Distro has a specific, well defined structure which makes it
##    unique and separates from analogous projects. This structure expected
##    to be followed during whole project lifetime. If you have ideas
##    how to restructure the PRoot-Distro, then please keep them in your
##    own fork! Source customization is not welcome.
##
## 5.1 PRoot-Distro is written in Bash script language with using features
##     which are not compatible with POSIX shell. The script itself is
##     monolithic and is not supposed to be split on multiple parts.
##
## 5.2 The tabs must be used for indenting code blocks everywhere.
##
## 5.3 All features are split on functions. There are 3 types of functions:
##
##     * Commands: implement specific features of PRoot-Distro such as
##       install, backup, login the distribution. These functions have
##       following name scheme: 'command_name()'.
##     * Command help: supplementary functions implementing informational
##       output describing usage of PRoot-Distro commands. These functions
##       are named as 'command_name_help()' and are defined AFTER the
##       commands.
##     * Utility functions: reusable or very long pieces of code.
##
## 5.4 Each command function definition should begin with comment section.
##
##     Comment sections logically separate functions on groups and provide
##     a brief overview which part of PRoot-Distro is implemented here. The
##     comment must provide a description of how the function works.
##
##     Utility functions are allowed to have a very brief description and
##     command help functions.
##
##     Command help functions should not have comments. They are self
##     descriptive.
##
## 5.5 Code pieces that do non-obvious actions must be commented.
##
## 5.6 Functions are not allowed to define global variables. Use 'local'
##     keyword to define the local variables instead.
##
## 5.7 Global variables must be defined either at beginning of PRoot-Distro
##     script or at the entry point.
##
## 5.8 Use of getopt for options handling is considered as non-flexible
##     and thus not allowed.
##
## 5.9 The checks for error conditions must be implemented. If error has
##     been encountered, a message should be printed on stderr and further
##     execution should be terminated.
##
## 5.10 If error was encountered inside function, exiting should be done
##      using the command 'return 1' to pass a status code to the caller.
##      Use of exit command is not allowed unless used in script entry point.
##
## 5.11 User input in command functions must be validated during the
##      command line arguments handling.
##
## 5.12 Use heredocs to write files, especially if their content is long.
##
## 5.13 Certain configuration such as plug-in directory path may be set only
##      during installation of the PRoot-Distro. Such configuration is
##      treated as persistent and should not be changed by user.
##
## 5.14 PRoot-Distro uses string place holders to define certain common
##      values for some variables:
##
##      * @TERMUX_APP_PACKAGE@ - sets Termux app package (com.termux).
##      * @TERMUX_PREFIX@ - sets the installation prefix path.
##      * @TERMUX_HOME@ - sets the Termux home directory path.
##
##      It is not allowed to hardcode the mentioned values instead of using
##      the place holder.
##
## ***
##
## 6. All informational messages are printed in specific format.
##
## 6.1 Use function 'msg' to print messages.
##
## 6.2 Messages printed by help functions must fit in 76 columns. If the
##     message is too long, split in on multiple lines. This is not relevant
##     to errors and other informational messages. Pay extra attention to
##     keep the message properly indented.
##
## 6.3 All messages printable by PRoot-Distro script must support colored
##     text through the escape sequences defined below by variables such as
##     RED (red), BRED (bold red text), YELLOW, BYELLOW, etc.
##
## 6.4 PRoot-Distro operation errors must have bold red color (${BRED}).
##     The key information such as arguments caused an error should be
##     encloded in quotes and highlighted by yellow color (${YELLOW}).
##
## 6.5 Messages should be terminated by ${RST} which resets the text colors
##     and other attributes.
##
## 6.7 Informational messages produced by PRoot-Distro command steps must
##     have the following format:
##
##     ${BLUE}[${GREEN}*${BLUE}] ${CYAN}Message...${RST}"
##
## 6.8 If the step encountered a condition where a warning or error should
##     be printed, the following message format should be used instead:
##
##     ${BLUE}[${RED}!${BLUE}] ${CYAN}Warning message.${RST}
##
## ***
##
## 7. Handling distributions.
##
## 7.1 PRoot-Distro script is intended to be distribution-agnostic. That
##     means it treats all distributions equally and handles them in the
##     same way despite the possible differences at level of distribition
##     root file system package.
##
## 7.2 Support of specific distribution is enabled by using a plug-in. The
##     plug-in is a Bash script that is sourced by PRoot-Distro and has the
##     following format:
##
##     DISTRO_NAME="Example"
##     DISTRO_COMMENT="Example distribution."
##
##     TARBALL_STRIP_OPT=1
##
##     TARBALL_URL['aarch64']="https://example.com/archive-aarch64.tar.gz"
##     TARBALL_URL['arm']="https://example.com/archive-armv7.tar.gz"
##     TARBALL_URL['i686']="https://example.com/archive-i386.tar.gz"
##     TARBALL_URL['x86_64']="https://example.com/archive-amd64.tar.gz"
##
##     TARBALL_SHA256['aarch64']="0000000000000000000000000000000000000000000000000000000000000000"
##     TARBALL_SHA256['arm']="0000000000000000000000000000000000000000000000000000000000000000"
##     TARBALL_SHA256['i686']="0000000000000000000000000000000000000000000000000000000000000000"
##     TARBALL_SHA256['x86_64']="0000000000000000000000000000000000000000000000000000000000000000"
##
##     distro_setup() {
##         run_proot_cmd touch /etc/hello-world
##         run_proot_cmd bash -c "echo '127.0.0.1 hello-world' >> /etc/hosts"
##     }
##
## 7.4 The variable DISTRO_NAME specifies a full name of distribution such
##     as "Ubuntu" or "Debian (stable)". This variable is mandatory.
##
## 7.3 The variable TARBALL_URL is a Bash associative array which contains
##     URLs to distribution rootfs tarball for given CPU architectures.
##     This variable is mandatory.
##
## 7.4 The variable TARBALL_SHA256 is a Bash associative array which
##     contains SHA-256 checksums of rootfs tarballs for given CPU
##     architectures. This variable is mandatory.
##
## 7.5 Post-installation steps that may be required for some distributions
##     are defined in 'distro_setup()' function (optional). This function
##     has access to all variables defined by PRoot-Distro during the
##     execution of 'command_install()'.
##
## 7.6 Commands inside 'distro_setup()' that are intended to be executed in
##     distribution environment must be defined as arguments of the
##     command 'run_proot_cmd'.
##
## 7.7 Distributions must be adressed by their alias which in fact is a
##     file name of plug-in except the extension part.
##
## 7.8 The alias files are located in $PREFIX/etc/proot-distro and have
##     extensions .sh or .override.sh.
##
##     * dist.sh name format is used for standard plug-ins.
##     * dist2.override.sh name is used to indicate that this is a
##       renamed distribution created by command 'rename' or by the
##       option '--override-alias' of command 'install'.
##
## 7.9 The alias for distribution must be unique and PRoot-Distro should
##     take care of that. User should not end with having two plug-ins
##     for same alias, for example ubuntu.sh and ubuntu.override.sh.
##
## 7.10 The rootfs for distribution may be extracted only under PRoot
##      session with active link2symlink extension.
##
## ***
##
## 8. Project versioning: major.minor.patch
##
## 8.1 Major version should be incremented when breaking changes were
##     released. Examples are deprecated features, changed locations of
##     files, command line format changes.
##
## 8.2 Minor version is incremented for significant but non-breaking
##     changes such as added new features or upgraded distributions. The
##     minor version is set to 0 when major version was incremented.
##
## 8.3 Patch version is incremented for small changes such as improvements
##     to existing features and bug fixes. It is set to 0 when major or
##     minor versions were incremented.
##
## ***
##
## 9. Warranty disclaimer.
##
## 9.1 PRoot-Distro has been created with aim to solve a specific range
##     of tasks and its functionality may not align with your needs.
##
## 9.2 PRoot-Distro is a Bash script wrapper for PRoot. If PRoot does not
##     work on your device, the PRoot-Distro will not work here as well.
##     You shall not beg the authors of PRoot-Distro to fix the PRoot. Such
##     requests will be ignored.
##
## 9.3 Certain functionality of PRoot-Distro relies on the Internet and
##     services like GitHub. If you do not have the Internet connection or
##     the GitHub is blocked by your ISP, then you may not be able to
##     install distributions. Please do not beg for setting up a censorship
##     resistant distribution tarball hosts.
##
## 9.4 The distributions are provided as-is, possibly with small changes
##     to set of preinstalled packages and their configuration. Bugs that
##     may exist in a specific distribution will not be fixed by authors
##     of the PRoot-Distro.
##
## 9.5 PRoot-Distro authors make the final decision whether to accept
##     submitted patches (pull requests) or not.
##
## 9.6 PRoot-Distro authors do not provide the time frames for fullfilling
##     a specific request (new feature, etc).
##
## 9.7 PRoot-Distro does not have upgrade schedule. Authors work on the
##     project development whenever have opportunity and desire to do so.
##     Releases are submitted when ready. There no alpha or beta builds.
##
#############################################################################

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

# Default name servers.
DEFAULT_PRIMARY_NAMESERVER="8.8.8.8"
DEFAULT_SECONDARY_NAMESERVER="8.8.4.4"

# Default fake kernel version.
# Note: faking kernel version is required when using PRoot-Distro on
# old devices that are not compatible with up-to-date versions of GNU libc.
DEFAULT_FAKE_KERNEL_VERSION="6.2.1-PRoot-Distro"

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

# Disable termux-exec or other things which may interfere with proot.
# It is expected that all dependencies have fixed hardcoded paths according
# to Termux file system layout.
unset LD_PRELOAD

#############################################################################
#
# FUNCTION TO PRINT A MESSAGE TO CONSOLE
#
# Prints a given text string to stderr. Supports escape sequences.
#
#############################################################################

msg() {
	echo -e "$@" >&2
}

#############################################################################
#
# DEPENDENCY CHECK
#
# Make sure all needed utilities are available in PATH before continuing.
#
#############################################################################

for i in awk basename bzip2 cat chmod cp curl cut du find grep gzip \
	head id mkdir proot rm sed tar xargs xz; do
	if [ -z "$(command -v "$i")" ]; then
		msg
		msg "${BRED}Utility '${i}' is not installed. Cannot continue.${RST}"
		msg
		exit 1
	fi
done
unset i

#############################################################################
#
# ANTI ROOT FUSE
#
# This script should never be executed as root as can mess up the ownership,
# and SELinux labels in $PREFIX.
#
#############################################################################

if [ "$(id -u)" = "0" ]; then
	msg
	msg "${BRED}Error: ${PROGRAM_NAME} should not be executed as root user.${RST}"
	msg
	exit 1
fi

#############################################################################
#
# ANTI NESTED PROOT FUSE
#
# Nested PRoot usage leads to performance degradation and other issues.
#
#############################################################################

TRACER_PID=$(grep TracerPid "/proc/$$/status" | cut -d $'\t' -f 2)
if [ "$TRACER_PID" != 0 ]; then
	TRACER_NAME=$(grep Name "/proc/${TRACER_PID}/status" | cut -d $'\t' -f 2)
	if [ "$TRACER_NAME" = "proot" ]; then
		msg
		msg "${BRED}Error: ${PROGRAM_NAME} should not be executed under PRoot.${RST}"
		msg
		exit 1
	fi
	unset TRACER_NAME
fi
unset TRACER_PID

#############################################################################
#
# FUNCTION TO INSTALL THE SPECIFIED DISTRIBUTION
#
# Brief algorithm how it works:
#
#  1. Process arguments supplied to 'install' command.
#  2. Ensure that requested distribution is supported and is not installed.
#  3. Source the distribution configuration plug-in.
#  4. Download the tarball of rootfs for requested distribution unless found
#     in cache.
#  5. Verify SHA-256 checksum of the rootfs tarball.
#  6. Extract the rootfs under PRoot with link2symlink extension enabled.
#  7. Perform post-installation actions on distribution to make it ready.
#
#############################################################################

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
			-h|--help)
				command_install_help
				return 0
				;;
			--override-alias)
				if [ $# -ge 2 ]; then
					shift 1

					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: argument to option '${YELLOW}--override-alias${BRED}' should not be empty.${RST}"
						command_install_help
						return 1
					fi

					if ! grep -qP '^[a-z0-9][a-z0-9_.+\-]*$' <<< "$1"; then
						msg
						msg "${BRED}Error: argument to option '${YELLOW}--override-alias${BRED}' should start only with an alphanumeric character and consist of alphanumeric characters including symbols '_.+-'."
						msg
						return 1
					fi

					if grep -qP '^.*\.sh$' <<< "$1"; then
						msg
						msg "${BRED}Error: argument to option '${YELLOW}--override-alias${BRED}' should not end with '.sh'.${RST}"
						msg
						return 1
					fi

					override_alias="$1"
				else
					msg
					msg "${BRED}Error: option '${YELLOW}--override-alias${BRED}' requires an argument.${RST}"
					command_install_help
					return 1
				fi
				;;
			-*)
				msg
				msg "${BRED}Error: got unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_install_help
				return 1
				;;
			*)
				if [ -z "${distro_name-}" ]; then
					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: distribution alias argument should not be empty.${RST}"
						command_install_help
						return 1
					fi
					distro_name="$1"
				else
					msg
					msg "${BRED}Error: got excessive positional argument '${YELLOW}${1}${BRED}'. Note that distribution can be specified only once.${RST}"
					command_install_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -z "${distro_name-}" ]; then
		msg
		msg "${BRED}Error: distribution alias is not specified.${RST}"
		command_install_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		msg
		msg "${BRED}Error: unknown distribution '${YELLOW}${distro_name}${BRED}' was requested to be installed.${RST}"
		msg
		msg "${CYAN}Run '${GREEN}${PROGRAM_NAME} list${CYAN}' to see the supported distributions.${RST}"
		msg
		return 1
	fi

	if [ -n "${override_alias-}" ]; then
		if [ ! -e "${DISTRO_PLUGINS_DIR}/${override_alias}.sh" ] && [ ! -e "${DISTRO_PLUGINS_DIR}/${override_alias}.override.sh" ]; then
			msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating file '${DISTRO_PLUGINS_DIR}/${override_alias}.override.sh'...${RST}"
			distro_plugin_script="${DISTRO_PLUGINS_DIR}/${override_alias}.override.sh"
			cp "${DISTRO_PLUGINS_DIR}/${distro_name}.sh" "${distro_plugin_script}"
			sed -i "s/^\(DISTRO_NAME=\)\(.*\)\$/\1\"${SUPPORTED_DISTRIBUTIONS["$distro_name"]} - ${override_alias}\"/g" "${distro_plugin_script}"
			SUPPORTED_DISTRIBUTIONS["${override_alias}"]="${SUPPORTED_DISTRIBUTIONS["$distro_name"]}"
			distro_name="${override_alias}"
		else
			msg
			msg "${BRED}Error: distribution with alias '${YELLOW}${override_alias}${BRED}' already exists.${RST}"
			msg
			return 1
		fi
	else
		distro_plugin_script="${DISTRO_PLUGINS_DIR}/${distro_name}.sh"

		# Try an alternate distribution name.
		if [ ! -f "${distro_plugin_script}" ]; then
			distro_plugin_script="${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh"
		fi
	fi

	if [ -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
		msg
		msg "${BRED}Error: distribution '${YELLOW}${distro_name}${BRED}' is already installed.${RST}"
		msg
		msg "${CYAN}Log in:     ${GREEN}${PROGRAM_NAME} login ${distro_name}${RST}"
		msg "${CYAN}Reinstall:  ${GREEN}${PROGRAM_NAME} reset ${distro_name}${RST}"
		msg "${CYAN}Uninstall:  ${GREEN}${PROGRAM_NAME} remove ${distro_name}${RST}"
		msg
		return 1
	fi

	if [ -f "${distro_plugin_script}" ]; then
		# Notify user if tar available in PATH is not GNU tar.
		if ! grep -q 'tar (GNU tar)' <(tar --version 2>/dev/null | head -n 1); then
			msg
			msg "${BRED}Warning: tar binary that is available in PATH appears to be not a GNU tar. You may experience issues during installation, backup and restore operations.${RST}"
			msg
		fi

		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Installing ${YELLOW}${SUPPORTED_DISTRIBUTIONS["$distro_name"]}${CYAN}...${RST}"

		# Make sure things are cleared up on failure or user requested exit.
		trap "echo -e \"\\r\\e[2K${BLUE}[${RED}!${BLUE}] ${CYAN}Exiting due to failure.${RST}\"; rm -rf \"${INSTALLED_ROOTFS_DIR:?}/${distro_name:?}\"; [ -e \"${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh\" ] && rm -f \"${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh\"; exit 1;" EXIT
		trap "trap - EXIT; echo -e \"\\r\\e[2K${BLUE}[${RED}!${BLUE}] ${CYAN}Exiting immediately as requested.${RST}\"; rm -rf \"${INSTALLED_ROOTFS_DIR:?}/${distro_name:?}\"; [ -e \"${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh\" ] && rm -f \"${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh\"; exit 1;" HUP INT TERM

		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating directory '${INSTALLED_ROOTFS_DIR}/${distro_name}'...${RST}"
		mkdir -m 755 -p "${INSTALLED_ROOTFS_DIR}/${distro_name}"

		export PROOT_L2S_DIR="${INSTALLED_ROOTFS_DIR}/${distro_name}/.l2s"
		if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/.l2s" ]; then
			echo -e "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating directory '${PROOT_L2S_DIR}'...${RST}"
			mkdir -p "$PROOT_L2S_DIR"
		fi

		# This should be overridden in distro plug-in with valid URL for
		# each architecture where possible.
		TARBALL_URL["aarch64"]=""
		TARBALL_URL["arm"]=""
		TARBALL_URL["i686"]=""
		TARBALL_URL["x86_64"]=""

		# This should be overridden in distro plug-in with valid SHA-256
		# for corresponding tarballs.
		TARBALL_SHA256["aarch64"]=""
		TARBALL_SHA256["arm"]=""
		TARBALL_SHA256["i686"]=""
		TARBALL_SHA256["x86_64"]=""

		# If your content inside tarball isn't stored in subdirectory,
		# you can override this variable in distro plug-in with 0.
		TARBALL_STRIP_OPT=1

		# Distribution plug-in contains steps on how to get download URL
		# and further post-installation configuration.
		source "${distro_plugin_script}"

		# Cannot proceed without URL and SHA-256.
		if [ -z "${TARBALL_URL["$DISTRO_ARCH"]}" ]; then
			msg "${BLUE}[${RED}!${BLUE}] ${CYAN}The distribution download URL is not defined for CPU architecture '${DISTRO_ARCH}'.${RST}"
			return 1
		fi
		if ! grep -qP '^[0-9a-fA-F]{64}$' <<< "${TARBALL_SHA256["$DISTRO_ARCH"]}"; then
			msg
			msg "${BRED}Error: got malformed SHA-256 from plug-in script '${distro_plugin_script}'.${RST}"
			msg
			return 1
		fi

		if [ ! -d "$DOWNLOAD_CACHE_DIR" ]; then
			msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating directory '$DOWNLOAD_CACHE_DIR'...${RST}"
			mkdir -p "$DOWNLOAD_CACHE_DIR"
		fi

		local tarball_name
		tarball_name=$(basename "${TARBALL_URL["$DISTRO_ARCH"]}")

		if [ ! -f "${DOWNLOAD_CACHE_DIR}/${tarball_name}" ]; then
			msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Downloading rootfs tarball...${RST}"

			# Using temporary file as script can't distinguish the partially
			# downloaded file from the complete. Useful in case if curl will
			# fail for some reason.
			msg
			rm -f "${DOWNLOAD_CACHE_DIR}/${tarball_name}.tmp"
			if ! curl --disable --fail --retry 5 --retry-connrefused --retry-delay 5 --location \
				--output "${DOWNLOAD_CACHE_DIR}/${tarball_name}.tmp" "${TARBALL_URL["$DISTRO_ARCH"]}"; then
				msg
				msg "${BLUE}[${RED}!${BLUE}] ${CYAN}Download failure, please check your network connection.${RST}"
				rm -f "${DOWNLOAD_CACHE_DIR}/${tarball_name}.tmp"
				return 1
			fi
			msg

			# If curl finished successfully, rename file to original.
			mv -f "${DOWNLOAD_CACHE_DIR}/${tarball_name}.tmp" "${DOWNLOAD_CACHE_DIR}/${tarball_name}"
		else
			msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Using cached rootfs tarball...${RST}"
		fi

		if [ -n "${TARBALL_SHA256["$DISTRO_ARCH"]}" ]; then
			msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Checking integrity, please wait...${RST}"
			local actual_sha256
			actual_sha256=$(sha256sum "${DOWNLOAD_CACHE_DIR}/${tarball_name}" | awk '{ print $1}')

			if [ "${TARBALL_SHA256["$DISTRO_ARCH"]}" != "${actual_sha256}" ]; then
				msg "${BLUE}[${RED}!${BLUE}] ${CYAN}Integrity checking failed. Try to redo installation again.${RST}"
				rm -f "${DOWNLOAD_CACHE_DIR}/${tarball_name}"
				return 1
			fi
		else
			msg "${BLUE}[${RED}!${BLUE}] ${CYAN}Integrity checking of downloaded rootfs has been disabled.${RST}"
		fi

		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Extracting rootfs, please wait...${RST}"
		# --exclude='dev' - need to exclude /dev directory which may contain device files.
		# --delay-directory-restore - set directory permissions only when files were extracted
		#                             to avoid issues with Arch Linux bootstrap archives.
		set +e
		proot --link2symlink \
			tar -C "${INSTALLED_ROOTFS_DIR}/${distro_name}" --warning=no-unknown-keyword \
			--delay-directory-restore --preserve-permissions --strip="${TARBALL_STRIP_OPT}" \
			-xf "${DOWNLOAD_CACHE_DIR}/${tarball_name}" --exclude='dev' |& grep -v "/linkerconfig/" >&2
		set -e

		# Write important environment variables to profile file as /bin/login does not
		# preserve them.
		local profile_script
		if [ -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile.d" ]; then
			profile_script="${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile.d/termux-proot.sh"
		else
			chmod u+rw "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile" >/dev/null 2>&1 || true
			profile_script="${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/profile"
		fi
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating file '${profile_script}'...${RST}"
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
		[ -z "\$LANG" ] && export LANG=C.UTF-8
		export PATH=\${PATH}:@TERMUX_PREFIX@/bin:/system/bin:/system/xbin
		export TERM=${TERM-xterm-256color}
		export TMPDIR=/tmp
		export PULSE_SERVER=127.0.0.1
		export MOZ_FAKE_NO_SANDBOX=1
		EOF

		# Default /etc/resolv.conf may be empty or unsuitable for use.
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating file '${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/resolv.conf'...${RST}"
		rm -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/resolv.conf"
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/resolv.conf"
		nameserver ${DEFAULT_PRIMARY_NAMESERVER}
		nameserver ${DEFAULT_SECONDARY_NAMESERVER}
		EOF

		# Default /etc/hosts may be empty or incomplete.
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating file '${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/hosts'...${RST}"
		chmod u+rw "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/hosts" >/dev/null 2>&1 || true
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/hosts"
		# IPv4.
		127.0.0.1   localhost.localdomain localhost

		# IPv6.
		::1         localhost.localdomain localhost ip6-localhost ip6-loopback
		fe00::0     ip6-localnet
		ff00::0     ip6-mcastprefix
		ff02::1     ip6-allnodes
		ff02::2     ip6-allrouters
		ff02::3     ip6-allhosts
		EOF

		# Add Android-specific UIDs/GIDs to /etc/group and /etc/gshadow.
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Registering Android-specific UIDs and GIDs...${RST}"
		chmod u+rw "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/passwd" \
			"${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/shadow" \
			"${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/group" \
			"${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/gshadow" >/dev/null 2>&1 || true
		echo "aid_$(id -un):x:$(id -u):$(id -g):Termux:/:/sbin/nologin" >> \
			"${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/passwd"
		echo "aid_$(id -un):*:18446:0:99999:7:::" >> \
			"${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/shadow"
		local group_name group_id
		while read -r group_name group_id; do
			echo "aid_${group_name}:x:${group_id}:root,aid_$(id -un)" \
				>> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/group"
			if [ -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/gshadow" ]; then
				echo "aid_${group_name}:*::root,aid_$(id -un)" \
					>> "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/gshadow"
			fi
		done < <(paste <(id -Gn | tr ' ' '\n') <(id -G | tr ' ' '\n'))

		# Ensure that proot will be able to bind fake /proc entries.
		setup_fake_proc

		# Run optional distro-specific hook.
		if declare -f -F distro_setup >/dev/null 2>&1; then
			msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Running distribution-specific configuration steps...${RST}"
			(cd "${INSTALLED_ROOTFS_DIR}/${distro_name}"
				distro_setup
			)
		fi

		# Reset trap for HUP/INT/TERM.
		trap - EXIT
		trap 'echo -e "\\r\\e[2K${BLUE}[${RED}!${BLUE}] ${CYAN}Exiting immediately as requested.${RST}"; exit 1;' HUP INT TERM

		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Finished.${RST}"
		msg
		msg "${CYAN}Now run '${GREEN}${PROGRAM_NAME} login $distro_name${CYAN}' to log in.${RST}"
		msg
		return 0
	else
		# Reset trap for HUP/INT/TERM.
		trap - EXIT
		trap 'echo -e "\\r\\e[2K${BLUE}[${RED}!${BLUE}] ${CYAN}Exiting immediately as requested.${RST}"; exit 1;' HUP INT TERM

		msg "${BLUE}[${RED}!${BLUE}] ${CYAN}Cannot find '${distro_plugin_script}' which is used to define a distribution properties.${RST}"
		return 1
	fi
}

# Special function for executing a command inside rootfs.
# Intended to be used inside plug-in distro_setup() function.
run_proot_cmd() {
	if [ -z "${distro_name-}" ]; then
		msg
		msg "${BRED}Error: called run_proot_cmd() but \${distro_name} is not set. Make sure that run_proot_cmd() is used inside distro_setup() function.${RST}"
		msg
		return 1
	fi

	if [ -z "${DISTRO_ARCH-}" ]; then
		msg
		msg "${BRED}Error: called run_proot_cmd() but \${DISTRO_ARCH} is not set.${RST}"
		msg
		return 1
	fi

	local qemu_arg=""
	if [ "$DISTRO_ARCH" != "$DEVICE_CPU_ARCH" ]; then
		local qemu_bin_path=""

		# If CPU and host OS are 64bit, we can run 32bit guest OS without emulation.
		# Everything else requires emulator (QEMU).
		case "$DISTRO_ARCH" in
			aarch64) qemu_bin_path="@TERMUX_PREFIX@/bin/qemu-aarch64";;
			arm)
				if [ "$DEVICE_CPU_ARCH" != "aarch64" ]; then
					qemu_bin_path="@TERMUX_PREFIX@/bin/qemu-arm"
				fi
				;;
			i686)
				if [ "$DEVICE_CPU_ARCH" != "x86_64" ]; then
					qemu_bin_path="@TERMUX_PREFIX@/bin/qemu-i386"
				fi
				;;
			x86_64) qemu_bin_path="@TERMUX_PREFIX@/bin/qemu-x86_64";;
			*)
				msg
				msg "${BRED}Error: DISTRO_ARCH has unknown value '$DISTRO_ARCH'. Valid values are: aarch64, arm, i686, x86_64."
				msg
				return 1
			;;
		esac

		if [ -n "$qemu_bin_path" ]; then
			if [ -x "$qemu_bin_path" ]; then
				qemu_arg="-q ${qemu_bin_path}"
			else
				local qemu_user_pkg=""
				case "$DISTRO_ARCH" in
					aarch64) qemu_user_pkg="qemu-user-aarch64";;
					arm) qemu_user_pkg="qemu-user-arm";;
					i686) qemu_user_pkg="qemu-user-i386";;
					x86_64) qemu_user_pkg="qemu-user-x86-64";;
					*) qemu_user_pkg="qemu-user-${DISTRO_ARCH}";;
				esac
				msg
				msg "${BRED}Error: package '${qemu_user_pkg}' is not installed.${RST}"
				msg
				return 1
			fi
		fi
	fi

	if [ -n "$qemu_arg" ]; then
		if [ -d "/apex" ]; then
			qemu_arg="${qemu_arg} --bind=/apex"
		fi
		if [ -e "/linkerconfig/ld.config.txt" ]; then
			qemu_arg="${qemu_arg} --bind=/linkerconfig/ld.config.txt"
		fi
		qemu_arg="${qemu_arg} --bind=@TERMUX_PREFIX@"
		qemu_arg="${qemu_arg} --bind=/system"
		qemu_arg="${qemu_arg} --bind=/vendor"
		if [ -f "/plat_property_contexts" ]; then
			qemu_arg="${qemu_arg} --bind=/plat_property_contexts"
		fi
		if [ -f "/property_contexts" ]; then
			qemu_arg="${qemu_arg} --bind=/property_contexts"
		fi
	fi

	proot \
		$qemu_arg -L \
		--kernel-release="${DEFAULT_FAKE_KERNEL_VERSION}" \
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
		--bind="${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.vmstat:/proc/vmstat" \
		/usr/bin/env -i \
			"HOME=/root" \
			"LANG=C.UTF-8" \
			"PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin" \
			"TERM=$TERM" \
			"TMPDIR=/tmp" \
			"$@"
}

# A function for preparing fake content for certain /proc entries which are
# known to have restricted access on Android OS. All entries are based on
# values retrieved from Arch Linux (x86_64) running on a VM with 8 CPUs and 8
# GiB of memory. Date 2023.03.28, Linux 6.2.1. Some values edited to fit
# the PRoot-Distro.
setup_fake_proc() {
	mkdir -p "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc"
	chmod 700 "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc"

	if [ ! -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.loadavg" ]; then
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.loadavg"
		0.12 0.07 0.02 2/165 765
		EOF
	fi

	if [ ! -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.stat" ]; then
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.stat"
		cpu  1957 0 2877 93280 262 342 254 87 0 0
		cpu0 31 0 226 12027 82 10 4 9 0 0
		cpu1 45 0 664 11144 21 263 233 12 0 0
		cpu2 494 0 537 11283 27 10 3 8 0 0
		cpu3 359 0 234 11723 24 26 5 7 0 0
		cpu4 295 0 268 11772 10 12 2 12 0 0
		cpu5 270 0 251 11833 15 3 1 10 0 0
		cpu6 430 0 520 11386 30 8 1 12 0 0
		cpu7 30 0 172 12108 50 8 1 13 0 0
		intr 127541 38 290 0 0 0 0 4 0 1 0 0 25329 258 0 5777 277 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0 0
		ctxt 140223
		btime 1680020856
		processes 772
		procs_running 2
		procs_blocked 0
		softirq 75663 0 5903 6 25375 10774 0 243 11685 0 21677
		EOF
	fi

	if [ ! -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.uptime" ]; then
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.uptime"
		124.08 932.80
		EOF
	fi

	if [ ! -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.version" ]; then
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.version"
		Linux version ${DEFAULT_FAKE_KERNEL_VERSION} (proot@termux) (gcc (GCC) 12.2.1 20230201, GNU ld (GNU Binutils) 2.40) #1 SMP PREEMPT_DYNAMIC Wed, 01 Mar 2023 00:00:00 +0000
		EOF
	fi

	if [ ! -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.vmstat" ]; then
		cat <<- EOF > "${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.vmstat"
		nr_free_pages 1743136
		nr_zone_inactive_anon 179281
		nr_zone_active_anon 7183
		nr_zone_inactive_file 22858
		nr_zone_active_file 51328
		nr_zone_unevictable 642
		nr_zone_write_pending 0
		nr_mlock 0
		nr_bounce 0
		nr_zspages 0
		nr_free_cma 0
		numa_hit 1259626
		numa_miss 0
		numa_foreign 0
		numa_interleave 720
		numa_local 1259626
		numa_other 0
		nr_inactive_anon 179281
		nr_active_anon 7183
		nr_inactive_file 22858
		nr_active_file 51328
		nr_unevictable 642
		nr_slab_reclaimable 8091
		nr_slab_unreclaimable 7804
		nr_isolated_anon 0
		nr_isolated_file 0
		workingset_nodes 0
		workingset_refault_anon 0
		workingset_refault_file 0
		workingset_activate_anon 0
		workingset_activate_file 0
		workingset_restore_anon 0
		workingset_restore_file 0
		workingset_nodereclaim 0
		nr_anon_pages 7723
		nr_mapped 8905
		nr_file_pages 253569
		nr_dirty 0
		nr_writeback 0
		nr_writeback_temp 0
		nr_shmem 178741
		nr_shmem_hugepages 0
		nr_shmem_pmdmapped 0
		nr_file_hugepages 0
		nr_file_pmdmapped 0
		nr_anon_transparent_hugepages 1
		nr_vmscan_write 0
		nr_vmscan_immediate_reclaim 0
		nr_dirtied 0
		nr_written 0
		nr_throttled_written 0
		nr_kernel_misc_reclaimable 0
		nr_foll_pin_acquired 0
		nr_foll_pin_released 0
		nr_kernel_stack 2780
		nr_page_table_pages 344
		nr_sec_page_table_pages 0
		nr_swapcached 0
		pgpromote_success 0
		pgpromote_candidate 0
		nr_dirty_threshold 356564
		nr_dirty_background_threshold 178064
		pgpgin 890508
		pgpgout 0
		pswpin 0
		pswpout 0
		pgalloc_dma 272
		pgalloc_dma32 261
		pgalloc_normal 1328079
		pgalloc_movable 0
		pgalloc_device 0
		allocstall_dma 0
		allocstall_dma32 0
		allocstall_normal 0
		allocstall_movable 0
		allocstall_device 0
		pgskip_dma 0
		pgskip_dma32 0
		pgskip_normal 0
		pgskip_movable 0
		pgskip_device 0
		pgfree 3077011
		pgactivate 0
		pgdeactivate 0
		pglazyfree 0
		pgfault 176973
		pgmajfault 488
		pglazyfreed 0
		pgrefill 0
		pgreuse 19230
		pgsteal_kswapd 0
		pgsteal_direct 0
		pgsteal_khugepaged 0
		pgdemote_kswapd 0
		pgdemote_direct 0
		pgdemote_khugepaged 0
		pgscan_kswapd 0
		pgscan_direct 0
		pgscan_khugepaged 0
		pgscan_direct_throttle 0
		pgscan_anon 0
		pgscan_file 0
		pgsteal_anon 0
		pgsteal_file 0
		zone_reclaim_failed 0
		pginodesteal 0
		slabs_scanned 0
		kswapd_inodesteal 0
		kswapd_low_wmark_hit_quickly 0
		kswapd_high_wmark_hit_quickly 0
		pageoutrun 0
		pgrotated 0
		drop_pagecache 0
		drop_slab 0
		oom_kill 0
		numa_pte_updates 0
		numa_huge_pte_updates 0
		numa_hint_faults 0
		numa_hint_faults_local 0
		numa_pages_migrated 0
		pgmigrate_success 0
		pgmigrate_fail 0
		thp_migration_success 0
		thp_migration_fail 0
		thp_migration_split 0
		compact_migrate_scanned 0
		compact_free_scanned 0
		compact_isolated 0
		compact_stall 0
		compact_fail 0
		compact_success 0
		compact_daemon_wake 0
		compact_daemon_migrate_scanned 0
		compact_daemon_free_scanned 0
		htlb_buddy_alloc_success 0
		htlb_buddy_alloc_fail 0
		cma_alloc_success 0
		cma_alloc_fail 0
		unevictable_pgs_culled 27002
		unevictable_pgs_scanned 0
		unevictable_pgs_rescued 744
		unevictable_pgs_mlocked 744
		unevictable_pgs_munlocked 744
		unevictable_pgs_cleared 0
		unevictable_pgs_stranded 0
		thp_fault_alloc 13
		thp_fault_fallback 0
		thp_fault_fallback_charge 0
		thp_collapse_alloc 4
		thp_collapse_alloc_failed 0
		thp_file_alloc 0
		thp_file_fallback 0
		thp_file_fallback_charge 0
		thp_file_mapped 0
		thp_split_page 0
		thp_split_page_failed 0
		thp_deferred_split_page 1
		thp_split_pmd 1
		thp_scan_exceed_none_pte 0
		thp_scan_exceed_swap_pte 0
		thp_scan_exceed_share_pte 0
		thp_split_pud 0
		thp_zero_page_alloc 0
		thp_zero_page_alloc_failed 0
		thp_swpout 0
		thp_swpout_fallback 0
		balloon_inflate 0
		balloon_deflate 0
		balloon_migrate 0
		swap_ra 0
		swap_ra_hit 0
		ksm_swpin_copy 0
		cow_ksm 0
		zswpin 0
		zswpout 0
		direct_map_level2_splits 29
		direct_map_level3_splits 0
		nr_unstable 0
		EOF
	fi
}

command_install_help() {
	msg
	msg "${BYELLOW}Usage: ${BCYAN}${PROGRAM_NAME} ${GREEN}install ${CYAN}[${GREEN}OPTIONS${CYAN}] [${GREEN}DISTRIBUTION ALIAS${CYAN}]${RST}"
	msg
	msg "${CYAN}Install a specified Linux distribution.${RST}"
	msg
	msg "${CYAN}Options:${RST}"
	msg
	msg "  ${GREEN}--help                         ${CYAN}- Show this help information.${RST}"
	msg
	msg "  ${GREEN}--override-alias [new alias]   ${CYAN}- Set a custom alias for installed${RST}"
	msg "                                   ${CYAN}distribution.${RST}"
	msg
	msg "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	msg "${CYAN}obtained by this command: ${GREEN}${PROGRAM_NAME} list${RST}"
	msg
	show_version
	msg
}

#############################################################################
#
# FUNCTION TO UNINSTALL SPECIFIED DISTRIBUTION
#
# Delete the rootfs of given distribution. If the associated plug-in has
# extension '.override.sh', it will be deleted as well.
#
#############################################################################

command_remove() {
	local distro_name

	while (($# >= 1)); do
		case "$1" in
			-h|--help)
				command_remove_help
				return 0
				;;
			-*)
				msg
				msg "${BRED}Error: got unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_remove_help
				return 1
				;;
			*)
				if [ -z "${distro_name-}" ]; then
					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: distribution alias argument should not be empty.${RST}"
						command_remove_help
						return 1
					fi
					distro_name="$1"
				else
					msg
					msg "${BRED}Error: got excessive positional argument '${YELLOW}${1}${BRED}'. Note that distribution can be specified only once.${RST}"
					command_remove_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -z "${distro_name-}" ]; then
		msg
		msg "${BRED}Error: distribution alias is not specified.${RST}"
		command_remove_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		msg
		msg "${BRED}Error: unknown distribution '${YELLOW}${distro_name}${BRED}' was requested to be removed.${RST}"
		msg
		msg "${CYAN}Run '${GREEN}${PROGRAM_NAME} list${CYAN}' to see the supported distributions.${RST}"
		msg
		return 1
	fi

	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
		msg
		msg "${BRED}Error: distribution '${YELLOW}${distro_name}${BRED}' is not installed.${RST}"
		msg
		return 1
	fi

	# The plug-ins created during renaming the distribution are considered
	# as generated content and should be deleted with rootfs.
	if [ "${CMD_REMOVE_REQUESTED_RESET-false}" = "false" ] && [ -e "${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh" ]; then
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Deleting file '${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh'...${RST}"
		rm -f "${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh"
	fi

	msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Wiping the rootfs of ${YELLOW}${SUPPORTED_DISTRIBUTIONS["$distro_name"]}${CYAN}...${RST}"
	# Attempt to restore permissions so directory can be removed without issues.
	chmod u+rwx -R "${INSTALLED_ROOTFS_DIR}/${distro_name}" > /dev/null 2>&1 || true
	# There is still chance for failure.
	if rm -rf "${INSTALLED_ROOTFS_DIR:?}/${distro_name:?}"; then
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Finished.${RST}"
	else
		msg "${BLUE}[${RED}!${BLUE}] ${CYAN}Finished with errors. Some files probably were not deleted.${RST}"
		return 1
	fi
}

command_remove_help() {
	msg
	msg "${BYELLOW}Usage: ${BCYAN}${PROGRAM_NAME} ${GREEN}remove ${CYAN}[${GREEN}DISTRIBUTION ALIAS${CYAN}]${RST}"
	msg
	msg "${CYAN}Remove a specified Linux distribution.${RST}"
	msg
	msg "${CYAN}Options:${RST}"
	msg
	msg "  ${GREEN}--help               ${CYAN}- Show this help information.${RST}"
	msg
	msg "${CYAN}Be careful when using it because you will not be prompted for${RST}"
	msg "${CYAN}confirmation and all data saved within the distribution will${RST}"
	msg "${CYAN}instantly gone.${RST}"
	msg
	msg "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	msg "${CYAN}obtained by this command: ${GREEN}${PROGRAM_NAME} list${RST}"
	msg
	show_version
	msg
}

#############################################################################
#
# FUNCTION TO RENAME A DISTRIBUTION
#
# Change the name of installed distribution by moving its rootfs directory
# and creating copy of original distrubution plug-in. The new plug-in will
# have an extension '.override.sh'.
#
#############################################################################

command_rename() {
	local orig_distro_name
	local new_distro_name

	while (($# >= 1)); do
		case "$1" in
			-h|--help)
				command_rename_help
				return 0
				;;
			-*)
				msg
				msg "${BRED}Error: got unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_rename_help
				return 1
				;;
			*)
				if [ -z "${orig_distro_name-}" ]; then
					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: original distribution alias argument should not be empty.${RST}"
						command_rename_help
						return 1
					fi
					orig_distro_name="$1"
				elif [ -z "${new_distro_name-}" ]; then
					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: new distribution alias argument should not be empty.${RST}"
						command_rename_help
						return 1
					fi
					new_distro_name="$1"
				else
					msg
					msg "${BRED}Error: got excessive positional argument '${YELLOW}${1}${BRED}'.${RST}"
					command_rename_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -z "${orig_distro_name-}" ]; then
		msg
		msg "${BRED}Error: the original alias of distribution is not specified.${RST}"
		command_rename_help
		return 1
	fi

	if [ -z "${new_distro_name-}" ]; then
		msg
		msg "${BRED}Error: the new alias of distribution is not specified.${RST}"
		command_rename_help
		return 1
	fi

	if [ "${orig_distro_name}" = "${new_distro_name}" ]; then
		msg
		msg "${BRED}Error: the original and new distribution aliases should not be same.${RST}"
		command_rename_help
		return 1
	fi

	# Put a restriction on characters in distribution name.
	# Same as for --override-alias option of command_install().
	if ! grep -qP '^[a-z0-9][a-z0-9_.+\-]*$' <<< "${new_distro_name}"; then
		msg
		msg "${BRED}Error: the new alias of distribution should start only with an alphanumeric character and consist of alphanumeric characters including symbols '_.+-'.${RST}"
		command_rename_help
		return 1
	fi

	if grep -qP '^.*\.sh$' <<< "${new_distro_name}"; then
		msg
		msg "${BRED}Error: the new alias of distribution should not end with '.sh'.${RST}"
		msg
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$orig_distro_name"]+x}" ]; then
		msg
		msg "${BRED}Error: unknown distribution '${YELLOW}${orig_distro_name}${BRED}' was requested to be renamed.${RST}"
		msg
		msg "${CYAN}Run '${GREEN}${PROGRAM_NAME} list${CYAN}' to see the supported distributions.${RST}"
		msg
		return 1
	fi

	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${orig_distro_name}" ]; then
		msg
		msg "${BRED}Error: cannot rename because the distribution '${YELLOW}${orig_distro_name}${BRED}' is not installed.${RST}"
		msg
		return 1
	fi

	if [ -d "${INSTALLED_ROOTFS_DIR}/${new_distro_name}" ]; then
		msg
		msg "${BRED}Error: cannot rename because the rootfs directory for distribution '${YELLOW}${new_distro_name}${BRED}' already exists.${RST}"
		msg
		return 1
	fi

	if [ -e "${DISTRO_PLUGINS_DIR}/${new_distro_name}.sh" ] || [ -e "${DISTRO_PLUGINS_DIR}/${new_distro_name}.override.sh" ]; then
		msg
		msg "${BRED}Error: distribution with alias '${YELLOW}${new_distro_name}${BRED}' already exists.${RST}"
		msg
		return 1
	fi

	if [ -e "${DISTRO_PLUGINS_DIR}/${orig_distro_name}.override.sh" ]; then
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Renaming '${DISTRO_PLUGINS_DIR}/${orig_distro_name}.override.sh' to '${DISTRO_PLUGINS_DIR}/${new_distro_name}.override.sh'...${RST}"
		mv "${DISTRO_PLUGINS_DIR}/${orig_distro_name}.override.sh" "${DISTRO_PLUGINS_DIR}/${new_distro_name}.override.sh"
		sed -i "s/^\(DISTRO_NAME=\)\"\(.*\) - ${orig_distro_name}\"\$/\1\"\2 - ${new_distro_name}\"/g" "${DISTRO_PLUGINS_DIR}/${new_distro_name}.override.sh"
	elif [ -e "${DISTRO_PLUGINS_DIR}/${orig_distro_name}.sh" ]; then
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Creating file '${DISTRO_PLUGINS_DIR}/${new_distro_name}.override.sh'...${RST}"
		cp "${DISTRO_PLUGINS_DIR}/${orig_distro_name}.sh" "${DISTRO_PLUGINS_DIR}/${new_distro_name}.override.sh"
		sed -i "s/^\(DISTRO_NAME=\)\(.*\)\$/\1\"${SUPPORTED_DISTRIBUTIONS["$orig_distro_name"]} - ${new_distro_name}\"/g" "${DISTRO_PLUGINS_DIR}/${new_distro_name}.override.sh"
	else
		msg
		msg "${BRED}Error: could not find a plug-in for distribution '${YELLOW}${orig_distro_name}${BRED}'.${RST}"
		msg
		return 1
	fi

	msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Renaming '${INSTALLED_ROOTFS_DIR}/${orig_distro_name}' to '${INSTALLED_ROOTFS_DIR}/${new_distro_name}'...${RST}"
	mv "${INSTALLED_ROOTFS_DIR}/${orig_distro_name}" "${INSTALLED_ROOTFS_DIR}/${new_distro_name}"
	msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Finished.${RST}"
}

command_rename_help() {
	msg
	msg "${BYELLOW}Usage: ${BCYAN}${PROGRAM_NAME} ${GREEN}rename ${CYAN}[${GREEN}ORIG ALIAS${CYAN}] [${GREEN}NEW ALIAS${CYAN}]${RST}"
	msg
	msg "${CYAN}Rename a specified Linux distribution.${RST}"
	msg
	msg "${CYAN}Note that renaming default distribution will create a copy of${RST}"
	msg "${CYAN}its plug-in.${RST}"
	msg
	msg "${CYAN}Options:${RST}"
	msg
	msg "  ${GREEN}--help               ${CYAN}- Show this help information.${RST}"
	msg
	msg "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	msg "${CYAN}obtained by this command: ${GREEN}${PROGRAM_NAME} list${RST}"
	msg
	show_version
	msg
}

#############################################################################
#
# FUNCTION TO REINSTALL THE GIVEN DISTRIBUTION
#
# A wrapper unifying functions command_remove && command_install.
#
#############################################################################

command_reset() {
	local distro_name

	while (($# >= 1)); do
		case "$1" in
			-h|--help)
				command_reset_help
				return 0
				;;
			-*)
				msg
				msg "${BRED}Error: got unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_reset_help
				return 1
				;;
			*)
				if [ -z "${distro_name-}" ]; then
					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: distribution alias argument should not be empty.${RST}"
						command_reset_help
						return 1
					fi
					distro_name="$1"
				else
					msg
					msg "${BRED}Error: got excessive positional argument '${YELLOW}${1}${BRED}'. Note that distribution can be specified only once.${RST}"
					command_reset_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -z "${distro_name-}" ]; then
		msg
		msg "${BRED}Error: distribution alias is not specified.${RST}"
		command_reset_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		msg
		msg "${BRED}Error: unknown distribution '${YELLOW}${distro_name}${BRED}' was requested to be reset.${RST}"
		msg
		msg "${CYAN}Run '${GREEN}${PROGRAM_NAME} list${CYAN}' to see the supported distributions.${RST}"
		msg
		return 1
	fi

	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
		msg
		msg "${BRED}Error: distribution '${YELLOW}${distro_name}${BRED}' is not installed.${RST}"
		msg
		return 1
	fi

	CMD_REMOVE_REQUESTED_RESET="true" command_remove "$distro_name"
	command_install "$distro_name"
}

command_reset_help() {
	msg
	msg "${BYELLOW}Usage: ${BCYAN}$PROGRAM_NAME ${GREEN}reset ${CYAN}[${GREEN}DISTRIBUTION ALIAS${CYAN}]${RST}"
	msg
	msg "${CYAN}Reinstall the specified Linux distribution.${RST}"
	msg
	msg "${CYAN}Options:${RST}"
	msg
	msg "  ${GREEN}--help               ${CYAN}- Show this help information.${RST}"
	msg
	msg "${CYAN}Be careful when using it because you will not be prompted for${RST}"
	msg "${CYAN}confirmation and all data saved within the distribution will${RST}"
	msg "${CYAN}instantly gone.${RST}"
	msg
	msg "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	msg "${CYAN}obtained by this command: ${GREEN}${PROGRAM_NAME} list${RST}"
	msg
	show_version
	msg
}

#############################################################################
#
# FUNCTION TO START SHELL OR EXECUTE COMMAND
#
# Starts root shell inside the rootfs of specified Linux distribution.
#
# If '--' with further arguments was specified, then execute command line
# given after '--' as root user without starting interactive shell.
#
#############################################################################

command_login() {
	local fix_low_ports=false
	local isolated_environment=false
	local use_termux_home=false
	local make_host_tmp_shared=false
	local -a custom_fs_bindings
	local no_link2symlink=false
	local no_sysvipc=false
	local no_kill_on_exit=false
	local login_user="root"
	local kernel_release="${DEFAULT_FAKE_KERNEL_VERSION}"
	local distro_name

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
			--termux-home)
				use_termux_home=true
				;;
			--shared-tmp)
				make_host_tmp_shared=true
				;;
			--bind)
				if [ $# -ge 2 ]; then
					shift 1

					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: argument to option '${YELLOW}--bind${BRED}' should not be empty.${RST}"
						command_login_help
						return 1
					fi

					custom_fs_bindings+=("$1")
				else
					msg
					msg "${BRED}Error: option '${YELLOW}--bind${BRED}' requires an argument.${RST}"
					command_login_help
					return 1
				fi
				;;
			--no-link2symlink)
				no_link2symlink=true
				;;
			--no-sysvipc)
				no_sysvipc=true
				;;
			--no-kill-on-exit)
				no_kill_on_exit=true
				;;
			--user)
				if [ $# -ge 2 ]; then
					shift 1

					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: argument to option '${YELLOW}--user${BRED}' should not be empty.${RST}"
						command_login_help
						return 1
					fi

					login_user="$1"
				else
					msg
					msg "${BRED}Error: option '${YELLOW}--user${BRED}' requires an argument.${RST}"
					command_login_help
					return 1
				fi
				;;
			--kernel)
				if [ $# -ge 2 ]; then
					shift 1

					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: argument to option '${YELLOW}--kernel${BRED}' should not be empty.${RST}"
						command_login_help
						return 1
					fi

					kernel_release="$1"
				else
					msg
					msg "${BRED}Error: option '${YELLOW}$1${BRED}' requires an argument.${RST}"
					command_login_help
					return 1
				fi
				;;
			-*)
				msg
				msg "${BRED}Error: got unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_login_help
				return 1
				;;
			*)
				if [ -z "${distro_name-}" ]; then
					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: distribution alias argument should not be empty.${RST}"
						command_login_help
						return 1
					fi
					distro_name="$1"
				else
					msg
					msg "${BRED}Error: got excessive positional argument '${YELLOW}${1}${BRED}'. Note that distribution can be specified only once.${RST}"
					command_login_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -z "${distro_name-}" ]; then
		msg
		msg "${BRED}Error: distribution alias is not specified.${RST}"
		command_login_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		msg
		msg "${BRED}Error: unknown distribution '${YELLOW}${distro_name}${BRED}' was requested for logging in.${RST}"
		msg
		msg "${CYAN}Run '${GREEN}${PROGRAM_NAME} list${CYAN}' to see the supported distributions.${RST}"
		msg
		return 1
	fi

	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
		msg
		msg "${BRED}Error: distribution '${YELLOW}${distro_name}${BRED}' is not installed.${RST}"
		msg
		return 1
	fi

	if [ -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/.l2s" ]; then
		export PROOT_L2S_DIR="${INSTALLED_ROOTFS_DIR}/${distro_name}/.l2s"
	fi

	if [ $# -ge 1 ]; then
		# Wrap in quotes each argument to prevent word splitting.
		local -a shell_command_args
		for i in "$@"; do
			shell_command_args+=("'$i'")
		done

		if stat "${INSTALLED_ROOTFS_DIR}/${distro_name}/bin/su" >/dev/null 2>&1; then
			set -- "/bin/su" "-l" "$login_user" "-c" "${shell_command_args[*]}"
		else
			msg "${BRED}Warning: no /bin/su available in rootfs! You may need to install package 'util-linux' or 'shadow' (shadow-utils) or equivalent, depending on distribution.${RST}"
			if [ -x "${INSTALLED_ROOTFS_DIR}/${distro_name}/bin/bash" ]; then
				set -- "/bin/bash" "-l" "-c" "${shell_command_args[*]}"
			else
				set -- "/bin/sh" "-l" "-c" "${shell_command_args[*]}"
			fi
		fi
	else
		if stat "${INSTALLED_ROOTFS_DIR}/${distro_name}/bin/su" >/dev/null 2>&1; then
			set -- "/bin/su" "-l" "$login_user"
		else
			msg "${BRED}Warning: no /bin/su available in rootfs! You may need to install package 'util-linux' or 'shadow' (shadow-utils) or equivalent, depending on distribution.${RST}"
			if [ -x "${INSTALLED_ROOTFS_DIR}/${distro_name}/bin/bash" ]; then
				set -- "/bin/bash" "-l"
			else
				set -- "/bin/sh" "-l"
			fi
		fi
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

	# Setup QEMU when CPU architecture do not match the one of device.
	local target_arch
	if [ -f "${DISTRO_PLUGINS_DIR}/${distro_name}.sh" ]; then
		target_arch=$(. "${DISTRO_PLUGINS_DIR}/${distro_name}.sh"; echo "${DISTRO_ARCH}")
	elif [ -f "${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh" ]; then
		target_arch=$(. "${DISTRO_PLUGINS_DIR}/${distro_name}.override.sh"; echo "${DISTRO_ARCH}")
	else
		# This should never happen.
		msg
		msg "${BRED}Error: missing plugin for distribution '${YELLOW}${distro_name}${BRED}'.${RST}"
		msg
		return 1
	fi

	local need_qemu=false
	if [ "$target_arch" != "$DEVICE_CPU_ARCH" ]; then
		local qemu_bin_path=""
		need_qemu=true

		# If CPU and host OS are 64bit, we can run 32bit guest OS without emulation.
		# Everything else requires emulator (QEMU).
		case "$target_arch" in
			aarch64) qemu_bin_path="@TERMUX_PREFIX@/bin/qemu-aarch64";;
			arm)
				if [ "$DEVICE_CPU_ARCH" != "aarch64" ]; then
					qemu_bin_path="@TERMUX_PREFIX@/bin/qemu-arm"
				else
					need_qemu=false
				fi
				;;
			i686)
				if [ "$DEVICE_CPU_ARCH" != "x86_64" ]; then
					qemu_bin_path="@TERMUX_PREFIX@/bin/qemu-i386"
				else
					need_qemu=false
				fi
				;;
			x86_64) qemu_bin_path="@TERMUX_PREFIX@/bin/qemu-x86_64";;
			*)
				msg
				msg "${BRED}Error: DISTRO_ARCH has unknown value '$target_arch'. Valid values are: aarch64, arm, i686, x86_64."
				msg
				return 1
			;;
		esac

		if [ -n "$qemu_bin_path" ]; then
			if [ -x "$qemu_bin_path" ]; then
				set -- "-q" "$qemu_bin_path" "$@"
			else
				local qemu_user_pkg=""
				case "$target_arch" in
					aarch64) qemu_user_pkg="qemu-user-aarch64";;
					arm) qemu_user_pkg="qemu-user-arm";;
					i686) qemu_user_pkg="qemu-user-i386";;
					x86_64) qemu_user_pkg="qemu-user-x86-64";;
					*) qemu_user_pkg="qemu-user-$target_arch";;
				esac

				msg
				msg "${BRED}Error: package '${qemu_user_pkg}' is not installed.${RST}"
				msg
				return 1
			fi
		fi
	fi

	if ! $no_kill_on_exit; then
		# This option terminates all background processes on exit, so
		# proot can terminate freely.
		set -- "--kill-on-exit" "$@"
	else
		msg "${BRED}Warning: option '--no-kill-on-exit' is enabled. When exiting, your session will be blocked until all processes are terminated.${RST}"
	fi

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
	set -- "--kernel-release=$kernel_release" "$@"

	# Fix lstat to prevent dpkg symlink size warnings
	set -- "-L" "$@"

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

	# Fake /proc/vmstat if necessary.
	if ! cat /proc/vmstat > /dev/null 2>&1; then
		set -- "--bind=${INSTALLED_ROOTFS_DIR}/${distro_name}/proc/.vmstat:/proc/vmstat" "$@"
	fi

	# Bind /tmp to /dev/shm.
	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp" ]; then
		mkdir -p "${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp"
		chmod 1777 "${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp"
	fi
	set -- "--bind=${INSTALLED_ROOTFS_DIR}/${distro_name}/tmp:/dev/shm" "$@"

	# When running in non-isolated mode, provide some bindings specific
	# to Android and Termux so user can interact with host file system.
	if ! $isolated_environment; then
		for data_dir in "/data/app" "/data/dalvik-cache"; do
			local dir_mode
			dir_mode=$(stat --format='%a' "$data_dir")
			if [[ ${dir_mode:2} =~ ^[157]$ ]]; then
				set -- "--bind=${data_dir}" "$@"
			fi
		done
		unset data_dir

		set -- "--bind=/data/data/@TERMUX_APP_PACKAGE@/cache" "$@"
		if [ -d "/data/data/@TERMUX_APP_PACKAGE@/files/apps" ]; then
			set -- "--bind=/data/data/@TERMUX_APP_PACKAGE@/files/apps" "$@"
		fi
		set -- "--bind=@TERMUX_HOME@" "$@"

		# Bind whole /storage directory when it is readable. This gives
		# access to shared storage and on some Android versions to external
		# disks such as SD cards. On failure try binding only shared
		# storage.
		if ls -1U /storage > /dev/null 2>&1; then
			set -- "--bind=/storage" "$@"
			set -- "--bind=/storage/emulated/0:/sdcard" "$@"
		else
			# We want to use the primary shared storage mount point
			# there with avoiding secondary and legacy mount points. As
			# Android OS versions are different, some directories may
			#be unavailable and we need to try them all.
			local storage_path
			if ls -1U /storage/self/primary/ > /dev/null 2>&1; then
				storage_path="/storage/self/primary"
			elif ls -1U /storage/emulated/0/ > /dev/null 2>&1; then
				storage_path="/storage/emulated/0"
			elif ls -1U /sdcard/ > /dev/null 2>&1; then
				storage_path="/sdcard"
			else
				# Shared storage is not accessible.
				storage_path=""
			fi

			if [ -n "$storage_path" ]; then
				set -- "--bind=${storage_path}:/sdcard" "$@"
				set -- "--bind=${storage_path}:/storage/emulated/0" "$@"
				set -- "--bind=${storage_path}:/storage/self/primary" "$@"
			fi
		fi
	fi

	# When using QEMU, we need some host files even in isolated mode.
	if ! $isolated_environment || $need_qemu; then
		local system_mnt
		for system_mnt in /apex /product /system /system_ext /vendor \
			/linkerconfig/ld.config.txt /plat_property_contexts \
			/property_contexts; do

			[ ! -e "$system_mnt" ] && continue

			local dir_mode
			dir_mode=$(stat --format='%a' "$system_mnt")
			if [[ ${dir_mode:2} =~ ^[157]$ ]]; then
				set -- "--bind=${system_mnt}" "$@"
			fi
		done
		set -- "--bind=@TERMUX_PREFIX@" "$@"
	fi

	# Use Termux home directory if requested.
	# Ignores --isolated.
	if $use_termux_home; then
		if [ "$login_user" = "root" ]; then
			set -- "--bind=@TERMUX_HOME@:/root" "$@"
		else
			if [ -f "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/passwd" ]; then
				local user_home
				user_home=$(grep -P "^${login_user}:" "${INSTALLED_ROOTFS_DIR}/${distro_name}/etc/passwd" | cut -d: -f 6)

				if [ -z "$user_home" ]; then
					user_home="/home/${login_user}"
				fi

				set -- "--bind=@TERMUX_HOME@:${user_home}" "$@"
			else
				set -- "--bind=@TERMUX_HOME@:/home/${login_user}" "$@"
			fi
		fi
	fi

	# Bind the tmp folder from the host system to the guest system
	# Ignores --isolated.
	if $make_host_tmp_shared; then
		set -- "--bind=@TERMUX_PREFIX@/tmp:/tmp" "$@"
	fi

	# Bind custom file systems.
	local bnd
	for bnd in "${custom_fs_bindings[@]}"; do
		set -- "--bind=${bnd}" "$@"
	done

	# Modify bindings to protected ports to use a higher port number.
	if $fix_low_ports; then
		set -- "-p" "$@"
	fi

	exec proot "$@"
}

command_login_help() {
	msg
	msg "${BYELLOW}Usage: ${BCYAN}$PROGRAM_NAME ${GREEN}login ${CYAN}[${GREEN}OPTIONS${CYAN}] [${GREEN}DISTRO ALIAS${CYAN}] [${GREEN}-- ${CYAN}[${GREEN}COMMAND${CYAN}]]${RST}"
	msg
	msg "${CYAN}Launch a login shell for the specified distribution if no${RST}"
	msg "${CYAN}additional arguments were given. Otherwise execute the${RST}"
	msg "${CYAN}given command and exit.${RST}"
	msg
	msg "${CYAN}Options:${RST}"
	msg
	msg "  ${GREEN}--help               ${CYAN}- Show this help information.${RST}"
	msg
	msg "  ${GREEN}--user [user]        ${CYAN}- Login as specified user instead of 'root'.${RST}"
	msg
	msg "  ${GREEN}--fix-low-ports      ${CYAN}- Modify bindings to protected ports to use${RST}"
	msg "                         ${CYAN}a higher port number.${RST}"
	msg
	msg "  ${GREEN}--isolated           ${CYAN}- Run isolated environment without access${RST}"
	msg "                         ${CYAN}to host file system.${RST}"
	msg
	msg "  ${GREEN}--termux-home        ${CYAN}- Mount Termux home directory to /root.${RST}"
	msg "                         ${CYAN}Takes priority over '${GREEN}--isolated${CYAN}' option.${RST}"
	msg
	msg "  ${GREEN}--shared-tmp         ${CYAN}- Mount Termux temp directory to /tmp.${RST}"
	msg "                         ${CYAN}Takes priority over '${GREEN}--isolated${CYAN}' option.${RST}"
	msg
	msg "  ${GREEN}--bind [path:path]   ${CYAN}- Custom file system binding. Can be specified${RST}"
	msg "                         ${CYAN}multiple times.${RST}"
	msg "                         ${CYAN}Takes priority over '${GREEN}--isolated${CYAN}' option.${RST}"
	msg
	msg "  ${GREEN}--no-link2symlink    ${CYAN}- Disable hardlink emulation by proot.${RST}"
	msg "                         ${CYAN}Adviseable only on devices with SELinux${RST}"
	msg "                         ${CYAN}in permissive mode.${RST}"
	msg
	msg "  ${GREEN}--no-sysvipc         ${CYAN}- Disable System V IPC emulation by proot.${RST}"
	msg
	msg "  ${GREEN}--no-kill-on-exit    ${CYAN}- Wait until all running processes will finish${RST}"
	msg "                         ${CYAN}before exiting. This will cause proot to${RST}"
	msg "                         ${CYAN}freeze if you are running daemons.${RST}"
	msg
	msg "  ${GREEN}--kernel [string]    ${CYAN}- Set the kernel release and compatibility${RST}"
	msg "                         ${CYAN}level to string.${RST}"
	msg
	msg "${CYAN}Put '${GREEN}--${CYAN}' if you wish to stop command line processing and pass${RST}"
	msg "${CYAN}options as shell arguments.${RST}"
	msg
	msg "${CYAN}If no '${GREEN}--isolated${CYAN}' option given, the following host directories${RST}"
	msg "${CYAN}will be available:${RST}"
	msg
	msg "  ${CYAN}* ${YELLOW}/apex ${CYAN}(only Android 10+)${RST}"
	msg "  ${CYAN}* ${YELLOW}/data/dalvik-cache${RST}"
	msg "  ${CYAN}* ${YELLOW}/data/data/@TERMUX_APP_PACKAGE@${RST}"
	msg "  ${CYAN}* ${YELLOW}/sdcard${RST}"
	msg "  ${CYAN}* ${YELLOW}/storage${RST}"
	msg "  ${CYAN}* ${YELLOW}/system${RST}"
	msg "  ${CYAN}* ${YELLOW}/vendor${RST}"
	msg
	msg "${CYAN}This should be enough to get Termux utilities like termux-api or${RST}"
	msg "${CYAN}termux-open get working. If they do not work for some reason,${RST}"
	msg "${CYAN}check if these files are sourced by your shell:${RST}"
	msg
	msg "  ${CYAN}* ${YELLOW}/etc/profile.d/termux-proot.sh${RST}"
	msg "  ${CYAN}* ${YELLOW}/etc/profile${RST}"
	msg
	msg "${CYAN}Also check whether they define variables like ANDROID_DATA,${RST}"
	msg "${CYAN}ANDROID_ROOT, BOOTCLASSPATH and others which are usually set${RST}"
	msg "${CYAN}in Termux sessions.${RST}"
	msg
	msg "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	msg "${CYAN}obtained by this command: ${GREEN}${PROGRAM_NAME} list${RST}"
	msg
	show_version
	msg
}

#############################################################################
#
# FUNCTION TO LIST THE SUPPORTED DISTRIBUTIONS
#
# Print the summary of available distributions and their installation
# status. The information about distributions is read from plug-in files.
#
#############################################################################

command_list() {
	msg
	if [ -z "${!SUPPORTED_DISTRIBUTIONS[*]}" ]; then
		msg "${YELLOW}No distribution plug-ins found.${RST}"
		msg
		msg "${YELLOW}Please check the directory '${DISTRO_PLUGINS_DIR}' and create at least one distribution plug-in.${RST}"
	else
		msg "${CYAN}Supported distributions:${RST}"

		local i
		for i in $(echo "${!SUPPORTED_DISTRIBUTIONS[@]}" | tr ' ' '\n' | sort -d); do
			msg
			msg "  ${CYAN}* ${YELLOW}${SUPPORTED_DISTRIBUTIONS[$i]}${RST}"
			msg
			msg "    ${CYAN}Alias: ${YELLOW}${i}${RST}"
			if [ -d "${INSTALLED_ROOTFS_DIR}/${i}" ]; then
				msg "    ${CYAN}Installed: ${GREEN}yes${RST}"
			else
				msg "    ${CYAN}Installed: ${RED}no${RST}"
			fi
			if [ -n "${SUPPORTED_DISTRIBUTIONS_COMMENTS["${i}"]+x}" ]; then
				msg "    ${CYAN}Comment: ${SUPPORTED_DISTRIBUTIONS_COMMENTS["${i}"]}${RST}"
			fi
		done

		msg
		msg "${CYAN}Install selected one with: ${GREEN}${PROGRAM_NAME} install <alias>${RST}"
	fi
	msg
}

#############################################################################
#
# FUNCTION TO BACKUP A SPECIFIED DISTRIBUTION
#
# Backup a specified distribution installation by making a tarball that
# contains distribution rootfs and a corresponding plug-in file.
#
#############################################################################

command_backup() {
	local distro_name
	local tarball_file_path

	while (($# >= 1)); do
		case "$1" in
			-h|--help)
				command_backup_help
				return 0
				;;
			--output)
				if [ $# -ge 2 ]; then
					shift 1

					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: argument to option '${YELLOW}--output${BRED}' should not be empty.${RST}"
						command_backup_help
						return 1
					fi

					tarball_file_path="$1"
				else
					msg
					msg "${BRED}Error: option '${YELLOW}--output${BRED}' requires an argument.${RST}"
					command_backup_help
					return 1
				fi
				;;
			-*)
				msg
				msg "${BRED}Error: got unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_backup_help
				return 1
				;;
			*)
				if [ -z "${distro_name-}" ]; then
					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: distribution alias argument should not be empty.${RST}"
						command_backup_help
						return 1
					fi
					distro_name="$1"
				else
					msg
					msg "${BRED}Error: got excessive positional argument '${YELLOW}${1}${BRED}'. Note that distribution can be specified only once.${RST}"
					command_backup_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -z "${distro_name-}" ]; then
		msg
		msg "${BRED}Error: distribution alias is not specified.${RST}"
		command_backup_help
		return 1
	fi

	if [ -z "${SUPPORTED_DISTRIBUTIONS["$distro_name"]+x}" ]; then
		msg
		msg "${BRED}Error: unknown distribution '${YELLOW}${distro_name}${BRED}' was requested for backup.${RST}"
		msg
		msg "${CYAN}Run '${GREEN}${PROGRAM_NAME} list${CYAN}' to see the supported distributions.${RST}"
		msg
		return 1
	fi

	if [ ! -d "${INSTALLED_ROOTFS_DIR}/${distro_name}" ]; then
		msg
		msg "${BRED}Error: distribution '${YELLOW}${distro_name}${BRED}' is not installed.${RST}"
		msg
		return 1
	fi

	# Notify user if tar available in PATH is not GNU tar.
	if ! grep -q 'tar (GNU tar)' <(tar --version 2>/dev/null | head -n 1); then
		msg
		msg "${BRED}Warning: tar binary that is available in PATH appears to be not a GNU tar. You may experience issues during installation, backup and restore operations.${RST}"
		msg
	fi

	msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Backing up ${YELLOW}${SUPPORTED_DISTRIBUTIONS["$distro_name"]}${CYAN}...${RST}"

	if [ -z "${tarball_file_path-}" ]; then
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Tarball will be written to stdout.${RST}"

		if [ -t 1 ]; then
			msg
			msg "${BRED}Error: tarball cannot be printed to console. Please use option '${YELLOW}--output${BRED}' to specify a file or use pipe for sending the output to another program.${RST}"
			msg
			return 1
		fi
	else
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Tarball will be written to '${tarball_file_path}'.${RST}"

		if [ -d "$tarball_file_path" ]; then
			msg
			msg "${BRED}Error: cannot write to '${YELLOW}${tarball_file_path}${YELLOW}' because this path is a directory.${RST}"
			command_backup_help
			return 1
		fi

		if [ -f "$tarball_file_path" ]; then
			msg
			msg "${BRED}Error: file '${YELLOW}${tarball_file_path}${YELLOW}' already exists. Please specify a different name.${RST}"
			command_backup_help
			return 1
		fi
	fi

	msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Fixing file permissions in rootfs...${RST}"
	# Ensure we can read all files.
	find "${INSTALLED_ROOTFS_DIR}/${distro_name}" -type d -print0 | xargs -0 -r chmod u+rx
	find "${INSTALLED_ROOTFS_DIR}/${distro_name}" -type f -executable -print0 | xargs -0 -r chmod u+rx
	find "${INSTALLED_ROOTFS_DIR}/${distro_name}" -type f ! -executable -print0 | xargs -0 -r chmod u+r

	local distro_plugin_script="${distro_name}.sh"
	if [ ! -f "${DISTRO_PLUGINS_DIR}/${distro_plugin_script}" ]; then
		# Alt name.
		distro_plugin_script="${distro_name}.override.sh"

		# We already passed check for supported distributions but doing
		# this check anyway. Above step with fixing permissions takes enough
		# time to let significant changes on file system happen.
		if [ ! -f "${DISTRO_PLUGINS_DIR}/${distro_plugin_script}" ]; then
			msg
			msg "${BRED}Error: neither '${distro_name}.sh' nor '${distro_name}.override.sh' are available in directory '${DISTRO_PLUGINS_DIR}'.${RST}"
			msg
			return 1
		fi
	fi

	msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Archiving the rootfs and plug-in...${RST}"
	if [ -n "${tarball_file_path-}" ]; then
		trap "echo -e \"\\r\\e[2K${BLUE}[${RED}!${BLUE}] ${CYAN}Exiting due to failure.${RST}\"; rm -f \"${tarball_file_path:?}\"; exit 1;" EXIT
		trap "trap - EXIT; echo -e \"\\r\\e[2K${BLUE}[${RED}!${BLUE}] ${CYAN}Exiting immediately as requested.${RST}\"; rm -f \"${tarball_file_path:?}\"; exit 1;" HUP INT TERM
		tar -c --auto-compress \
			--warning=no-file-ignored \
			-f "$tarball_file_path" \
			-C "${DISTRO_PLUGINS_DIR}/../" "$(basename "$DISTRO_PLUGINS_DIR")/${distro_plugin_script}" \
			-C "${INSTALLED_ROOTFS_DIR}/../" "$(basename "$INSTALLED_ROOTFS_DIR")/${distro_name}"
		trap - EXIT
		trap 'echo -e "\\r\\e[2K${BLUE}[${RED}!${BLUE}] ${CYAN}Exiting immediately as requested.${RST}"; exit 1;' HUP INT TERM
	else
		tar -c \
			--warning=no-file-ignored \
			-C "${DISTRO_PLUGINS_DIR}/../" "$(basename "$DISTRO_PLUGINS_DIR")/${distro_plugin_script}" \
			-C "${INSTALLED_ROOTFS_DIR}/../" "$(basename "$INSTALLED_ROOTFS_DIR")/${distro_name}"
	fi
	msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Finished.${RST}"
}

command_backup_help() {
	msg
	msg "${BYELLOW}Usage: ${BCYAN}$PROGRAM_NAME ${GREEN}backup ${CYAN}[${GREEN}DISTRIBUTION ALIAS${CYAN}]${RST}"
	msg
	msg "${CYAN}Back up a specified distribution installation into tarball.${RST}"
	msg
	msg "${CYAN}Options:${RST}"
	msg
	msg "  ${GREEN}--help               ${CYAN}- Show this help information.${RST}"
	msg
	msg "  ${GREEN}--output [path]      ${CYAN}- Write tarball to specified file.${RST}"
	msg "                         ${CYAN}If not specified, the tarball will be${RST}"
	msg "                         ${CYAN}printed to stdout. File extension affects${RST}"
	msg "                         ${CYAN}used compression (e.g. gz, bz2, xz).${RST}"
	msg "                         ${CYAN}Backup sent to stdout is not compressed.${RST}"
	msg
	msg "${CYAN}Selected distribution should be referenced by alias which can be${RST}"
	msg "${CYAN}obtained by this command: ${GREEN}${PROGRAM_NAME} list${RST}"
	msg
	show_version
	msg
}

#############################################################################
#
# FUNCTION TO RESTORE A SPECIFIED DISTRIBUTION
#
# Restore a specified distribution installation from the backup (tarball).
# The supplied tarball should be one made by PRoot-Distro as it has a proper
# structure. Regular rootfs tarball will not work here.
#
#############################################################################

command_restore() {
	local tarball_file_path

	while (($# >= 1)); do
		case "$1" in
			-h|--help)
				command_restore_help
				return 0
				;;
			-*)
				msg
				msg "${BRED}Error: got unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_restore_help
				return 1
				;;
			*)
				if [ -z "${tarball_file_path-}" ]; then
					if [ -z "$1" ]; then
						msg
						msg "${BRED}Error: tarball file path argument should not be empty.${RST}"
						command_restore_help
						return 1
					fi
					tarball_file_path="$1"
				else
					msg
					msg "${BRED}Error: got excessive positional argument '${YELLOW}${1}${BRED}'. Note that tarball file path can be specified only once.${RST}"
					command_restore_help
					return 1
				fi
				;;
		esac
		shift 1
	done

	if [ -n "${tarball_file_path-}" ]; then
		if [ ! -e "$tarball_file_path" ]; then
			msg
			msg "${BRED}Error: file '${YELLOW}${tarball_file_path}${YELLOW}' does not exist.${RST}"
			command_restore_help
			return 1
		fi

		if [ -d "$tarball_file_path" ]; then
			msg
			msg "${BRED}Error: path '${YELLOW}${tarball_file_path}${YELLOW}' is a directory.${RST}"
			command_restore_help
			return 1
		fi
	else
		if [ -t 0 ]; then
			msg
			msg "${BRED}Error: tarball file path is not specified and it looks like nothing is being piped via stdin either.${RST}"
			command_restore_help
			return 1
		fi
	fi

	# Notify user if tar available in PATH is not GNU tar.
	if ! grep -q 'tar (GNU tar)' <(tar --version 2>/dev/null | head -n 1); then
		msg
		msg "${BRED}Warning: tar binary that is available in PATH appears to be not a GNU tar. You may experience issues during installation, backup and restore operations.${RST}"
		msg
	fi

	local success
	msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Extracting distribution plug-in and rootfs from the tarball...${RST}"
	if [ -n "${tarball_file_path-}" ]; then
		if mkdir -m 755 -p "${INSTALLED_ROOTFS_DIR}" && tar -x --auto-compress -f "$tarball_file_path" \
			--recursive-unlink --preserve-permissions \
			-C "${DISTRO_PLUGINS_DIR}/../" "$(basename "${DISTRO_PLUGINS_DIR}")/" \
			-C "${INSTALLED_ROOTFS_DIR}/../" "$(basename "${INSTALLED_ROOTFS_DIR}")/"; then
			success=true
		else
			success=false
		fi
	else
		if mkdir -m 755 -p "${INSTALLED_ROOTFS_DIR}" && tar -x --recursive-unlink --preserve-permissions \
			-C "${DISTRO_PLUGINS_DIR}/../" "$(basename "${DISTRO_PLUGINS_DIR}")/" \
			-C "${INSTALLED_ROOTFS_DIR}/../" "$(basename "${INSTALLED_ROOTFS_DIR}")/"; then
			success=true
		else
			success=false
		fi
	fi

	if $success; then
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Finished.${RST}"
	else
		msg "${BLUE}[${RED}!${BLUE}] ${CYAN}Failure.${RST}"
		msg
		msg "${BRED}Failed to restore distribution from the given tarball.${RST}"
		msg
		msg "${BRED}Possibly that tarball was corrupted or not made by PRoot-Distro. Note that tarball piped to stdin must be decompressed.${RST}"
		msg
	fi
}

command_restore_help() {
	msg
	msg "${BYELLOW}Usage: ${BCYAN}${PROGRAM_NAME} ${GREEN}restore ${CYAN}[${GREEN}FILENAME.TAR${CYAN}]${RST}"
	msg
	msg "${CYAN}Restore distribution installation from a specified tarball. If${RST}"
	msg "${CYAN}file name is not specified, it will be assumed that tarball is${RST}"
	msg "${CYAN}being piped from stdin.${RST}"
	msg
	msg "${CYAN}Options:${RST}"
	msg
	msg "  ${GREEN}--help               ${CYAN}- Show this help information.${RST}"
	msg
	msg "${CYAN}Archive compression is determined automatically from the file${RST}"
	msg "${CYAN}extension. When archive content is piped it is expected that${RST}"
	msg "${CYAN}data is not compressed.${RST}"
	msg
	msg "${CYAN}Important note: there are no any sanity check being performed${RST}"
	msg "${CYAN}on the supplied tarballs. Be careful when using this command as${RST}"
	msg "${CYAN}data loss may happen when the wrong tarball was used.${RST}"
	msg
	show_version
	msg
}

#############################################################################
#
# FUNCTION TO CLEAR DOWNLOAD CACHE
#
# Delete all cached rootfs tarballs.
#
#############################################################################

command_clear_cache() {
	while (($# >= 1)); do
		case "$1" in
			-h|--help)
				command_clear_cache_help
				return 0
				;;
			-*)
				msg
				msg "${BRED}Error: got unknown option '${YELLOW}${1}${BRED}'.${RST}"
				command_clear_cache_help
				return 1
				;;
			*)
				msg
				msg "${BRED}Error: got excessive positional argument '${YELLOW}${1}${BRED}'. Note that tarball file path can be specified only once.${RST}"
				command_clear_cache_help
				return 1
				;;
		esac
		shift 1
	done

	if ! ls -la "${DOWNLOAD_CACHE_DIR}"/* > /dev/null 2>&1; then
		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Download cache is empty.${RST}"
	else
		local size_of_cache
		size_of_cache="$(du -d 0 -h -a ${DOWNLOAD_CACHE_DIR} | awk '{$2=$2};1' | cut -d " " -f 1)"

		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Clearing cache files...${RST}"

		local filename
		while read -r filename; do
			msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Deleting ${CYAN}'${filename}'${RST}"
			rm -f "${filename}"
		done < <(find "${DOWNLOAD_CACHE_DIR}" -type f)

		msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Reclaimed ${size_of_cache} of disk space.${RST}"
	fi

	msg "${BLUE}[${GREEN}*${BLUE}] ${CYAN}Finished.${RST}"
}

command_clear_cache_help() {
	msg
	msg "${BYELLOW}Usage: ${BCYAN}${PROGRAM_NAME} ${GREEN}clear-cache${RST}"
	msg
	msg "${CYAN}Remove all cached rootfs tarballs to reclaim disk space.${RST}"
	msg
	show_version
	msg
}

#############################################################################
#
# FUNCTION TO PRINT UTILITY USAGE INFORMATION
#
# Prints a description of PRoot-Distro utility and list of the available
# commands.
#
#############################################################################

command_help() {
	msg
	msg "${BYELLOW}Usage: ${BCYAN}${PROGRAM_NAME}${CYAN} [${GREEN}COMMAND${CYAN}] [${GREEN}ARGUMENTS${CYAN}]${RST}"
	msg
	msg "${CYAN}PRoot-Distro is a Bash script wrapper for PRoot. It provides${RST}"
	msg "${CYAN}a set of functions with standardized command line interface${RST}"
	msg "${CYAN}to let user easily manage Linux PRoot containers. By default${RST}"
	msg "${CYAN}it supports a number of well known Linux distributions such${RST}"
	msg "${CYAN}Alpine Linux, Debian or OpenSUSE. However it is possible to${RST}"
	msg "${CYAN}add others with a help of plug-ins.${RST}"
	msg
	msg "${CYAN}List of the available commands:${RST}"
	msg
	msg "  ${GREEN}help         ${CYAN}- Show this help information.${RST}"
	msg
	msg "  ${GREEN}backup       ${CYAN}- Backup a specified distribution.${RST}"
	msg
	msg "  ${GREEN}install      ${CYAN}- Install a specified distribution.${RST}"
	msg
	msg "  ${GREEN}list         ${CYAN}- List supported distributions and their${RST}"
	msg "                 ${CYAN}installation status.${RST}"
	msg
	msg "  ${GREEN}login        ${CYAN}- Start login shell for the specified distribution.${RST}"
	msg
	msg "  ${GREEN}remove       ${CYAN}- Delete a specified distribution.${RST}"
	msg "                 ${RED}WARNING: this command destroys data!${RST}"
	msg
	msg "  ${GREEN}rename       ${CYAN}- Rename installed distribution.${RST}"
	msg
	msg "  ${GREEN}reset        ${CYAN}- Reinstall from scratch a specified distribution.${RST}"
	msg "                 ${RED}WARNING: this command destroys data!${RST}"
	msg
	msg "  ${GREEN}restore      ${CYAN}- Restore a specified distribution.${RST}"
	msg "                 ${RED}WARNING: this command destroys data!${RST}"
	msg
	msg "  ${GREEN}clear-cache  ${CYAN}- Clear cache of downloaded files. ${RST}"
	msg
	msg "${CYAN}Each of commands has its own help information. To view it, just${RST}"
	msg "${CYAN}supply a '${GREEN}--help${CYAN}' argument to chosen command.${RST}"
	msg
	msg "${CYAN}Hint: type command '${GREEN}${PROGRAM_NAME} list${CYAN}' to get a list of the${RST}"
	msg "${CYAN}supported distributions. Pick a distro alias and run the next${RST}"
	msg "${CYAN}command to install it: ${GREEN}${PROGRAM_NAME} install <alias>${RST}"
	msg
	msg "${CYAN}Runtime data is stored at this location:${RST}"
	msg "${CYAN}${RUNTIME_DIR}${RST}"
	msg
	msg "${CYAN}If you have issues with proot during installation or login, try${RST}"
	msg "${CYAN}to set '${GREEN}PROOT_NO_SECCOMP=1${CYAN}' environment variable.${RST}"
	msg
	show_version
	msg
}

#############################################################################
#
# FUNCTION TO PRINT VERSION STRING
#
# Prints version & author information. Used in functions for displaying
# usage info.
#
#############################################################################

show_version() {
	msg "${ICYAN}Proot-Distro v${PROGRAM_VERSION} by Termux.${RST}"
}

#############################################################################
#
# ENTRY POINT
#
# 1. Determine device properties such as CPU architecture.
# 2. Check all available distribution plug-ins.
# 3. Handle the requested commands or show help when '-h/--help/help' were
#    given. Further command line processing is offloaded to requested command.
#
#############################################################################

# This will be executed when signal HUP/INT/TERM is received.
trap 'echo -e "\\r${BLUE}[${RED}!${BLUE}] ${CYAN}Exiting immediately as requested.${RST}"; exit 1;' HUP INT TERM

# Determine a CPU architecture of device.
case "$(uname -m)" in
	# Note: armv8l means that device is running 32bit OS on 64bit CPU.
	armv7l|armv8l) DEVICE_CPU_ARCH="arm";;
	*) DEVICE_CPU_ARCH=$(uname -m);;
esac
DISTRO_ARCH=$DEVICE_CPU_ARCH

# Verify architecture if possible - avoid running under linux32 or similar.
if [ -x "@TERMUX_PREFIX@/bin/dpkg" ]; then
	if [ "$DEVICE_CPU_ARCH" != "$("@TERMUX_PREFIX@"/bin/dpkg --print-architecture)" ]; then
		msg
		msg "${BRED}Error: the CPU architecture reported by system does not match the architecture of Termux packages. Do not attempt to hijack system properties by using 'linux32' or similar utilities.${RST}"
		msg
		exit 1
	fi
fi

declare -A TARBALL_URL TARBALL_SHA256
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
		msg
		msg "${BRED}Error: no DISTRO_NAME defined in '${YELLOW}${filename}${BRED}'.${RST}"
		msg
		exit 1
	fi

	SUPPORTED_DISTRIBUTIONS["$distro_alias"]="$distro_name"
	[ -n "$distro_comment" ] && SUPPORTED_DISTRIBUTIONS_COMMENTS["$distro_alias"]="$distro_comment"
done < <(find "$DISTRO_PLUGINS_DIR" -maxdepth 1 -type f -iname "*.sh" 2>/dev/null)
unset distro_name distro_alias

if [ $# -ge 1 ]; then
	case "$1" in
		-h|--help|help) shift 1; command_help;;
		backup) shift 1; command_backup "$@";;
		install) shift 1; command_install "$@";;
		list) shift 1; command_list;;
		login) shift 1; command_login "$@";;
		remove) shift 1; CMD_REMOVE_REQUESTED_RESET="false" command_remove "$@";;
		rename) shift 1; command_rename "$@";;
		clear-cache) shift 1; command_clear_cache "$@";;
		reset) shift 1; command_reset "$@";;
		restore) shift 1; command_restore "$@";;
		*)
			msg
			msg "${BRED}Error: unknown command '${YELLOW}$1${BRED}'.${RST}"
			msg
			msg "${CYAN}Run '${GREEN}${PROGRAM_NAME} help${CYAN}' to see the list of available commands.${RST}"
			msg
			exit 1
			;;
	esac
else
	msg
	msg "${BRED}Error: no command provided.${RST}"
	command_help
fi

exit 0
