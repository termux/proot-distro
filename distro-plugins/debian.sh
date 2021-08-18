# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian (bullseye)"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v2.1.1/debian-aarch64-pd-v2.1.1.tar.xz"
TARBALL_SHA256['aarch64']="83e0ebc3e7ab7bcb3bc1f21516ec30b9dbc1953ee16e5647a9631223673eb34c"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v2.1.1/debian-arm-pd-v2.1.1.tar.xz"
TARBALL_SHA256['arm']="4244b8f9899ff24fe7352137ab31fd2fbe962992f76b1fc41cc9cf2cadbcd877"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v2.1.1/debian-i686-pd-v2.1.1.tar.xz"
TARBALL_SHA256['i686']="3ca53aa2d62f26e829892d3bf4d0a694de34acb5a0788ec30dafb24216e76881"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v2.1.1/debian-x86_64-pd-v2.1.1.tar.xz"
TARBALL_SHA256['x86_64']="c8a1daa9077bc0eb1addd8f2aee838d41c657ccb0114db04d91b9e44d9303d3b"

distro_setup() {
	# Include security & updates.
	cat <<- EOF > ./etc/apt/sources.list
	deb https://deb.debian.org/debian bullseye main contrib
	#deb https://deb.debian.org/debian-security/ bullseye/updates main contrib
	deb https://deb.debian.org/debian bullseye-updates main contrib
	EOF

	# Don't update gvfs-daemons and udisks2
	run_proot_cmd apt-mark hold gvfs-daemons udisks2
}
