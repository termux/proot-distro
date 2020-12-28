##
## Plug-in for installing Ubuntu Bionic.
##

DISTRO_NAME="Ubuntu 18.04"

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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-ubuntu-bionic-rootfs/ubuntu-bionic-core-cloudimg-arm64-root-2020.12.10.tar.gz"
			sha256="f65e67f1b2d5cab43548356eff4001f3a34210d66686ab46375fe13eddb5ac33"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-ubuntu-bionic-rootfs/ubuntu-bionic-core-cloudimg-armhf-root-2020.12.10.tar.gz"
			sha256="503c7828eeb4903c05d51e973e42fa9ce2f969e123ef60ad2a3cf4a09b3bfa2e"
			;;
		i686)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-ubuntu-bionic-rootfs/ubuntu-bionic-core-cloudimg-i386-root-2020.12.10.tar.gz"
			sha256="32356912ec3a3c4c2ac19c95107bd7dc01657ab0fd9ef86e7e29dcc167b4eed4"
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-ubuntu-bionic-rootfs/ubuntu-bionic-core-cloudimg-amd64-root-2020.12.10.tar.gz"
			sha256="8f78f4029da9e3343a01a386e1be6498f96f86685264baafefa6e8836d11fce9"
			;;
	esac

	echo "${sha256}|${rootfs}"
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
