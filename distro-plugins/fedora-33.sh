##
## Plug-in for installing Fedora 33.
##

DISTRO_NAME="Fedora 33"

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
	local dist_arch
	local sha256

	case "$DISTRO_ARCH" in
		aarch64)
			dist_arch="arm64"
			sha256="4c113d35b8793d43756ca0f77bf46c41853daafe04d7514b2217079e0c2aa2c5"
			;;
		armv7l|armv8l)
			dist_arch="armhf"
			sha256="47c911447e5912f133d26b6e8464007cdd169ca1c9a8b70ff1d4188c7be74e70"
			;;
		x86_64)
			dist_arch="amd64"
			sha256="4002904ef27aafbe63294dc12c1b96a42bb3ff1a8596e0c61ec28f6be3b10a53"
			;;
	esac

	echo "${sha256}|https://github.com/termux/proot-distro/releases/download/v1.4.0-fedora-rootfs/fedora-33-${dist_arch}-2021.01.10.tar.gz"
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
