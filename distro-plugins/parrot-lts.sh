##
## Plug-in for installing Parrot OS (LTS).
##

DISTRO_NAME="Parrot OS (LTS)"

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
			sha256="678e395321df1cc819ddf5f5fee7e83e4795e414dcb261ee6bfd2733a6c1c755"
			;;
		armv7l|armv8l)
			dist_arch="armhf"
			sha256="ecad84033e402a43c9209df3a0a181009fcd85f9ec27b2a3bbf02d1ea4f29174"
			;;
		i686)
			dist_arch="i386"
			sha256="9f093c3bfa44ae38538861374e62dc0d151ff2b8d96725c6f6305f7cae736445"
			;;
		x86_64)
			dist_arch="amd64"
			sha256="c2d561f89f05ba12408d554f1ac862d1054e76d81d054d27b2ee70bcd5d4d93d"
			;;
	esac

	echo "${sha256}|https://github.com/termux/proot-distro/releases/download/v1.4.0-parrot-rootfs/parrotos-lts-${dist_arch}-2021.01.10.tar.gz"
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
