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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/bionic-server-cloudimg-arm64-root.tar.xz"
			sha256="1f9ea68be35cc646017c0da6f1001bb0613bd7c540617aee8e9fd0d30e80078c"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/bionic-server-cloudimg-armhf-root.tar.xz"
			sha256="ce9533c6920f621e23f4e379a2f7c92568807187ac88e93cbd53f9ecee2d7899"
			;;
		i686)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/bionic-server-cloudimg-i386-root.tar.xz"
			sha256="55af22f3b181de25b9142f308713b8034b2851990ac0b30eabaeb9ccded4bc15"
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/bionic-server-cloudimg-amd64-root.tar.xz"
			sha256="e16108fd926cd170bc5de2797a66da99f3b10afd9706384c02ef3806297d17fc"
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
