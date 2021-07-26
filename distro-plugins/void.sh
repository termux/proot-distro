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
			rootfs="https://alpha.de.repo.voidlinux.org/live/current/void-aarch64-ROOTFS-20210218.tar.xz"
			sha256="82b33beeae9a06947e25bcbfc57ffd15bc9055cf92a64b96409113869219c3fa"
			;;
		armv6l)
		    rootfs="https://alpha.de.repo.voidlinux.org/live/current/void-armv6l-ROOTFS-20210218.tar.xz"
		    sha256="b0bd507f52b905b956f97d711721bcfd79059fd97e2c9eef738e6327eb164040"
	        ;;
		armv7l|armv8l)
			rootfs="https://alpha.de.repo.voidlinux.org/live/current/void-armv7l-ROOTFS-20210218.tar.xz"
			sha256="4ef61e05276cf4cb2141bbeaad8614db0a9ec7b709d966680791131ac9823240"
			;;
	    i686)
	        rootfs="https://alpha.de.repo.voidlinux.org/live/current/void-i686-ROOTFS-20210218.tar.xz"
	        sha256="7814ccf33dfd7a536ec48cc1550e94a1344311fddeb1981d6c5d49df1f1684f6"
		x86_64)
			rootfs="https://alpha.de.repo.voidlinux.org/live/current/void-x86_64-ROOTFS-20210218.tar.xz"
			sha256="66fb856557946da129354e22f1b2cfd93f8e1e56bcbcba8902a5e59dc309c3cd"
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
