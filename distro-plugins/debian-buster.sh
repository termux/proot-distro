##
## Plug-in for installing Debian 10 (Buster).
##

DISTRO_NAME="Debian 10 (Buster)"

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
	local deb_arch
	local sha256

	case "$DISTRO_ARCH" in
		aarch64)
			deb_arch="arm64"
			sha256="c9e87d21eea22d5eec3681508b84cf1f48dfeb2e7178c65c5bd755d56600936a"
			;;
		armv7l|armv8l)
			deb_arch="armhf"
			sha256="35f29b0568b1c1d7d9d1263245834d2dce8c5fa5b78b64c72a79349585b06690"
			;;
		i686)
			deb_arch="i386"
			sha256="c74b2b9771ba5ad693a2d8ee5600b0373ffad3f6a548f71bbb9516cd7b1d2e43"
			;;
		x86_64)
			deb_arch="amd64"
			sha256="968e753aafc2b94fd4f94729b3453d7515cf4ab647a6db38ed0b80af0e9c5720"
			;;
	esac

	echo "${sha256}|https://github.com/termux/proot-distro/releases/download/v1.4.0-debian-rootfs/debian-buster-${deb_arch}-2021.01.10.tar.gz"
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
