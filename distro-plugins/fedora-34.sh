##
## Plug-in for installing Fedora 34.
##

DISTRO_NAME="Fedora 34"

# Rootfs is in subdirectory.
DISTRO_TARBALL_STRIP_OPT=1

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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.10.1/fedora-aarch64-pd-v1.10.1.tar.xz"
			sha256="9584a6c02324adc989f63876c51a93f6ca752e0d5d533428d55210260230c32b"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.10.1/fedora-arm-pd-v1.10.1.tar.xz"
			sha256="1491ff7603f1007feaa07ebeee477ef492c6d400dc4404503c11fb5d9ab035f6"
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.10.1/fedora-x86_64-pd-v1.10.1.tar.xz"
			sha256="5c47d00c9a196285ac5993b99bc80d659c5244924f2457ef166e757a50cd4efc"
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
