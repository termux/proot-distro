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
			sha256="3dbe607b4d79ea8bc0a779c17f55d42231ff6f643c25f7be9cae48160674ebaf"
			;;
		armv7l|armv8l)
			deb_arch="armhf"
			sha256="2f9881d66192014964dbde8cf3d0cddba497a10312ad61e6f120b646da52bd6f"
			;;
		i686)
			deb_arch="i386"
			sha256="084dd00617e84514a718bfbf8434d1913a04a4985a3c487569a7de038afa1eda"
			;;
		x86_64)
			deb_arch="amd64"
			sha256="38d2ef257de4d3648b769f96802b2e99f07ee80ccc24e0bb093d80bcbdde0a73"
			;;
	esac

	echo "${sha256}|https://github.com/termux/proot-distro/releases/download/v1.1-debian-rootfs/debian-buster-${deb_arch}-2020.12.05.tar.gz"
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
