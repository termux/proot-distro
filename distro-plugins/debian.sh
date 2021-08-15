# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian (buster)"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v2.0.1/debian-aarch64-pd-v2.0.1.tar.xz"
TARBALL_SHA256['aarch64']="5293c7a3dcd23dcafa877cd14d7f4f80797a36d48042216170bb4683dcb66c3a"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v2.0.1/debian-arm-pd-v2.0.1.tar.xz"
TARBALL_SHA256['arm']="a1c1fd6755448ab42a184e03d8519d3b38039885e6bed79726538f933c50da23"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v2.0.1/debian-i686-pd-v2.0.1.tar.xz"
TARBALL_SHA256['i686']="53e56c2ae14164fd54a793c6db5c94a9b926f8c3825488e090c72e8cc42430b1"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v2.0.1/debian-x86_64-pd-v2.0.1.tar.xz"
TARBALL_SHA256['x86_64']="1a179871bc58e74ebe7e204c927c896ba95a2969cf7eb54a326bd968e2f6df30"

distro_setup() {
	# Include security & updates.
	cat <<- EOF > ./etc/apt/sources.list
	deb https://deb.debian.org/debian buster main contrib
	deb https://deb.debian.org/debian-security/ buster/updates main contrib
	deb https://deb.debian.org/debian buster-updates main contrib
	EOF

	# Don't update gvfs-daemons and udisks2
	run_proot_cmd apt-mark hold gvfs-daemons udisks2
}
