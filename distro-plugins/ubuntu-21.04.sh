##
## Plug-in for installing Ubuntu Hirsute.
##

DISTRO_NAME="Ubuntu 21.04"

# You can override a CPU architecture to let distribution
# be executed by QEMU (user-mode).
#
# You can specify the following values here:
#
#  * aarch64: AArch64 (ARM64, 64bit ARM)
#  * armv7l:  ARM (32bit)
#  * i686:    x86 (32bit)
#  * x86_64:  x86 (64bit)
#
# Default value is set by proot-distro script and is equal
# to the CPU architecture of your device (uname -m).
#DISTRO_ARCH=$(uname -m)

# Returns download URL and SHA-256 of file in this format:
# SHA-256|FILE-NAME
get_download_url() {
	local rootfs
	local sha256

	case "$DISTRO_ARCH" in
		aarch64)
			rootfs="https://github.com/RandomCoderOrg/ubuntu-on-android/releases/download/v1-raw/ubuntu-base-21.04-base-arm64.tar.gz"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/RandomCoderOrg/ubuntu-on-android/releases/download/v1-raw/ubuntu-base-21.04-base-armhf.tar.gz"
			;;

		x86_64)
			rootfs="https://github.com/RandomCoderOrg/ubuntu-on-android/releases/download/v1-raw/ubuntu-base-21.04-base-amd64.tar.gz"
			;;
	esac

	echo "${rootfs}"
}

# Define here additional steps which should be executed
# for configuration.
distro_setup() {
	# Hint: $PWD is the distribution rootfs directory.
	#echo "hello world" > ./etc/motd

	# Run command within proot'ed environment with
	# run_proot_cmd function.
	# Uncomment this to do system upgrade during installation.
	#run_proot_cmd apt update
	#run_proot_cmd apt upgrade -yq
	:
}
