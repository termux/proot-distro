##
## Plug-in for installing Gentoo (stage 3).
##

DISTRO_NAME="Gentoo"

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
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/stage3-arm64-20210805T040654Z.tar.xz"
			sha256="50828305e5c79c064703bf594b1ee71d4e5595a345cdbbab4bef6409d5aef023"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/stage3-armv7a-20210803T170546Z.tar.xz"
			sha256="4845dd4b2f7a3d2fe0f0375129074fcbcf575d294b21bf94323dc11b86cef94a"
			;;
		i686)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/stage3-i686-openrc-20210802T170539Z.tar.xz"
			sha256="162ffdd5ca4f9da074a3746e25db040534df80732e07cd7978624548a83caa5f"
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/stage3-amd64-openrc-20210801T170533Z.tar.xz"
			sha256="3a36c6df9ae61f1f1b8362094cf1f92d13d9f68c91eec172e52a80eb6aef929a"
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
