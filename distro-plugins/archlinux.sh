##
## Plug-in for installing Arch Linux.
##
## Warning: Arch Linux ARM is not Arch Linux! This is a different project,
## yet it is a lot similar to the original. Proot-Distro considers them as
## equal to make things easier.
##

DISTRO_NAME="Arch Linux"

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

# x86_64 rootfs is inside subdirectory.
if [ "$DISTRO_ARCH" = "x86_64" ]; then
	DISTRO_TARBALL_STRIP_OPT=1
fi

# Returns download URL and SHA-256 of file in this format:
# SHA-256|FILE-NAME
get_download_url() {
	local rootfs
	local sha256

	case "$DISTRO_ARCH" in
		aarch64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/ArchLinuxARM-aarch64-latest.tar.gz"
			sha256="13cdfea2c682fadfb2a6a8374beacedf0682cbc39238922a596de7c49fb90a67"
			;;
		armv7l|armv8l)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/ArchLinuxARM-armv7-latest.tar.gz"
			sha256="84c6424b9dee1359bc4a49e85f51da9db1239a2d88c55df72db5500043b69a53"
			;;
		x86_64)
			rootfs="https://github.com/termux/proot-distro/releases/download/v1.9.0-updated-distributions/archlinux-bootstrap-2021.08.01-x86_64.tar.gz"
			sha256="7ddf5717999549699d9a012608e2f8cae9fbe70e24dc03ee2c3065226955e206"
			;;
	esac

	echo "${sha256}|${rootfs}"
}

# Define here additional steps which should be executed
# for configuration.
distro_setup() {
	# Enable the first found mirror. Needed only for x86_64 as
	# ArchLinuxARM has geoip-based mirror enabled by default.
	if [ "$(uname -m)" = "x86_64" ]; then
		sed -i 's/#Server = http/Server = http/' ./etc/pacman.d/mirrorlist
	fi

	# Pacman keyring initialization.
	run_proot_cmd pacman-key --init
	if [ "$(uname -m)" = "x86_64" ]; then
		run_proot_cmd pacman-key --populate archlinux
	else
		run_proot_cmd pacman-key --populate archlinuxarm
	fi

	# Initialize en_US locale.
	echo "en_US.UTF-8 UTF-8" > ./etc/locale.gen
	run_proot_cmd locale-gen
	sed -i 's/LANG=C.UTF-8/LANG=en_US.UTF-8/' ./etc/profile.d/termux-proot.sh

	# Uninstall packages which are not necessary.
	case "$(uname -m)" in
		aarch64)
			run_proot_cmd pacman -Rnsc --noconfirm linux-aarch64
			;;
		armv7l|armv8l)
			run_proot_cmd pacman -Rnsc --noconfirm linux-armv7
			;;
	esac
}
