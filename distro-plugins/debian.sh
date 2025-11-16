# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian (trixie)"
DISTRO_COMMENT="Stable release."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/debian-trixie-aarch64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['aarch64']="3834a11cbc6496935760bdc20cca7e2c25724d0cd8f5e4926da8fd5ca1857918"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.29.0/debian-trixie-arm-pd-v4.29.0.tar.xz"
TARBALL_SHA256['arm']="99bcba87d8d1c66c0de06259ac0a270eb0a20f8b4af39beb0705d28846d78b90"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.29.0/debian-trixie-i686-pd-v4.29.0.tar.xz"
TARBALL_SHA256['i686']="a388a0531301b033ef5509ab6a50cc886f7f90e7ec9cac02569b45af1900229a"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/debian-trixie-x86_64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['x86_64']="4b8f33b80a10d734ff935e5934588572f860c0c38a68bf91db59af0580370716"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
