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
			sha256="130ce87b3c0cfc8a8942fa80f42133bd6ca3ff4dcc373b91276cb0d42c5ee46e"
			;;
		armv7l|armv8l)
			dist_arch="armhf"
			sha256="b85512291f33fe1b583e44f917c643805a8ba0fea32152091bbe72f6a93fd752"
			;;
		i686)
			dist_arch="i386"
			sha256="0a23e8a7099f1fc6ffde53bc2a37037d35908587762fdcf099936172e2442a0e"
			;;
		x86_64)
			dist_arch="amd64"
			sha256="b613656955014fc0bdc44f7014ee5e23fe25b25d42cc95aef0f4f7dd793bacc2"
			;;
	esac

	echo "${sha256}|https://github.com/termux/proot-distro/releases/download/v1.4.0-parrot-rootfs/parrotos-lts-${dist_arch}-2021.02.11.tar.gz"
}

# Define here additional steps which should be executed
# for configuration.
distro_setup() {
	sed -i 's@deb http@deb [trusted=yes] http@g' \
		./etc/apt/sources.list.d/parrot.list
}
