##
## Plug-in for installing Alpine Linux.
##

DISTRO_NAME="Alpine Linux 3.13.1"

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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-alpine-rootfs/alpine-minirootfs-3.13.1-aarch64.tar.gz"
			sha256="0e8258cc599a86be428228dd5ea9217959f2c77873790bec645c05899f252c5e"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-alpine-rootfs/alpine-minirootfs-3.13.1-armv7.tar.gz"
			sha256="71bedb0810b656377192f4a239126f452a016d7a33e28f8ad8f46e9b0e7d8028"
			;;
		i686)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-alpine-rootfs/alpine-minirootfs-3.13.1-x86.tar.gz"
			sha256="6884df8f8e44d462a9723e412f0b76124019683d98afe448a8767dc5f5f3a7e0"
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-alpine-rootfs/alpine-minirootfs-3.13.1-x86_64.tar.gz"
			sha256="1ab0a7c05107c4504018be170d937c07e35283b7a3f153886c8c93162dfbf4af"
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
	# Uncomment this to run 'apk upgrade' during installation.
	#run_proot_cmd apk upgrade
	:
}
