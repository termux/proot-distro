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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/focal-server-cloudimg-arm64-root.tar.xz"
			sha256="30f87b519fe6d381bb7fcf3f6ddf59c4bc1a7cc679c0440a547a7ad52ee4a898"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/hirsute-server-cloudimg-armhf-root.tar.xz"
			sha256="6ff4fad3263c46aeb3059f9849dc58183d1ea619dd22e8fdd907c697e93aa59b"
			;;
		i686)
			# Ubuntu Focal does not provide tarballs for x86 32bit.
			return
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/focal-server-cloudimg-amd64-root.tar.xz"
			sha256="86ec71e8ec6c7b40fa6da96618f720162ed96fc70fd974cc24a695072bfa3d35"
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
