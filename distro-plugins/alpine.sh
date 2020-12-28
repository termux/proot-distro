##
## Plug-in for installing Alpine Linux.
##

DISTRO_NAME="Alpine Linux 3.12.0"

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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-alpine-rootfs/alpine-minirootfs-3.12.0-aarch64.tar.gz"
			sha256="3f5c39b088ddaf44910eed4427f7d511b5e4aa7041943221baf2d4734c5fa60a"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-alpine-rootfs/alpine-minirootfs-3.12.0-armv7.tar.gz"
			sha256="d4842c7c65fed4291ca3eea7e50a918aec368b66663906220c4887eee14ae176"
			;;
		i686)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-alpine-rootfs/alpine-minirootfs-3.12.0-x86.tar.gz"
			sha256="f06ae2ed0b5f52457a9762ddfcd067f559d35f92b83b4d0a294e3001e5070a62"
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-alpine-rootfs/alpine-minirootfs-3.12.0-x86_64.tar.gz"
			sha256="0beb54cf9bf69d085f9fcd291ff28b3335184d08b706d535f425e8180851edc9"
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
