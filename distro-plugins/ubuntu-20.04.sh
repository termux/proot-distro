##
## Plug-in for installing Ubuntu Focal.
##

DISTRO_NAME="Ubuntu 20.04"

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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-ubuntu-focal-rootfs/ubuntu-focal-core-cloudimg-arm64-root-2020.12.10.tar.gz"
			sha256="426a0345245ab95491bc78073b7f2f2ea91acd65b001eb9d6b8709eb1a5ba642"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-ubuntu-focal-rootfs/ubuntu-focal-core-cloudimg-armhf-root-2020.12.10.tar.gz"
			sha256="eb9ac4f4ee33071d25e95cd6b62dedfb57aa2f9449c6160f46a27fbf78bc821e"
			;;
		i686)
			# Ubuntu Focal does not provide tarballs for x86 32bit.
			return
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-ubuntu-focal-rootfs/ubuntu-focal-core-cloudimg-amd64-root-2020.12.10.tar.gz"
			sha256="c7f50b2e87f172e0c0d1c3fe38fcfc7ca33d62d20ef41dc185e20d19e4d4aa59"
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
