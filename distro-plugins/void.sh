##
## Plug-in for installing Void Linux.
##

DISTRO_NAME="Void Linux"

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
			rootfs="https://alpha.de.repo.voidlinux.org/live/20210316/void-aarch64-ROOTFS-20210316.tar.xz"
			sha256="6ed8dc1d10e5700f2cf2c18a51dfbb3f8067636b65f20546b4c589d158483da8"
			;;
		armv6l)
			rootfs="https://alpha.de.repo.voidlinux.org/live/20210316/void-armv6l-ROOTFS-20210316.tar.xz"
			sha256="1806b13bee0d03fa4b26ddef722e411e6113eae2da70abd0989bfca1a4a89452"
			;;
		armv7l|armv8l)
			rootfs="https://alpha.de.repo.voidlinux.org/live/20210316/void-armv7l-ROOTFS-20210316.tar.xz"
			sha256="f26059cd39ed608194bf31a31db8890cafcb9a3f0dfc2145eefb785d9867d9fb"
			;;
		i686)
			rootfs="https://alpha.de.repo.voidlinux.org/live/20210316/void-i686-ROOTFS-20210316.tar.xz"
			sha256="c47dc4d522978bb866fb0e163bdc67126498bbf47a81c3f377d4b341cf5b8cfc"
			;;
		x86_64)
			rootfs="https://alpha.de.repo.voidlinux.org/live/20210316/void-x86_64-ROOTFS-20210316.tar.xz"
			sha256="a5b28d171aa8eeec7fd5a5b10cdcc9161749fbe5e1e74a00c67b379a85a7d1ed"
			;;
	esac

	echo "${sha256}|${rootfs}"
}

# Define here additional steps which should be executed
# for configuration.
distro_setup() {
	run_proot_cmd xbps-install -Su xbps <<EOF
y
EOF
	run_proot_cmd xbps-install -u <<EOF
y
EOF
	run_proot_cmd xbps-install base-system <<EOF
y
EOF
	run_proot_cmd xbps-remove base-voidstrap <<EOF
y
EOF

	echo "void" > ./etc/hostname
	run_proot_cmd xbps-reconfigure -fa

}
