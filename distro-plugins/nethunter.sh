##
## Plug-in for installing Kali Nethunter Rootless.
##

DISTRO_NAME="Kali Nethunter"
DISTRO_COMMENT="Minimal version, most of utilities should be installed manually."

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
	local rootfs
	local sha256

	case "$DISTRO_ARCH" in
		aarch64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-kali-rootfs/kalifs-arm64-2020.12.10-minimal.tar.xz"
			sha256="ec849f556768b9d9bdb541a32760ad2c00d73bf3aff8436e203cadb33d14bea3"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-kali-rootfs/kalifs-armhf-2020.12.10-minimal.tar.xz"
			sha256="365c4483fbef46624381c2f34a4e49080f8930564ba984971b56b373012fe866"
			;;
		i686)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-kali-rootfs/kalifs-i386-2020.12.10-minimal.tar.xz"
			sha256="c10ef435751a048479e43478bc5ac6da94647ffe2eed4869e4c5418643ea49c6"
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.2-kali-rootfs/kalifs-amd64-2020.12.10-minimal.tar.xz"
			sha256="a7de1ea316579f43902df17da2a52e52e03c540ed39fea20e982dc4e8c5ceb45"
			;;
	esac

	echo "${sha256}|${rootfs}"
}

# Define here additional steps which should be executed
# for configuration.
distro_setup() {
	# Fix ~/.bash_profile.
	cat <<- EOF > ./root/.bash_profile
	. /root/.bashrc
	. /root/.profile
	EOF
}
