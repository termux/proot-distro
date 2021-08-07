##
## Plug-in for installing Alpine Linux.
##

DISTRO_NAME="Alpine Linux 3.14.1"

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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/alpine-minirootfs-3.14.1-aarch64.tar.gz"
			sha256="63c3ca0cf9d870566ab35f273e4d19d6456bf8b9b22278d6990abd605c0baecf"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/alpine-minirootfs-3.14.1-armv7.tar.gz"
			sha256="5e051a4060c0a48d530bc514f614919501eba1b1b698b799e4beb5d2de6c0463"
			;;
		i686)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/alpine-minirootfs-3.14.1-x86.tar.gz"
			sha256="f154d232179433c5f3638948943f84d39d2dd137e26a53d21223eae1ffbcd663"
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/alpine-minirootfs-3.14.1-x86_64.tar.gz"
			sha256="2723a3ced7344f29d75c10328fd772ca68f4a6b39a4bade8b2347d58ccff3bae"
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
