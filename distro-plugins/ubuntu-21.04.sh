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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/hirsute-server-cloudimg-arm64-root.tar.xz"
			sha256="5934bf6796f4be30b9475085a5db712244a009048f4af159583e48823093f59c"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/hirsute-server-cloudimg-armhf-root.tar.xz"
			sha256="e67acd2d6f966576f76a65b0e652db9b1fdf43ad0ef7dd89de64c5c034d62ad4"
			;;
		i686)
			# Ubuntu Hirsute does not provide tarballs for x86 32bit.
			return
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/hirsute-server-cloudimg-amd64-root.tar.xz"
			sha256="2c6e5af90ed872c15c177f9ebe5265104a1cb1e2af7c943f6b131c29caadef6d"
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
