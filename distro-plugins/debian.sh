# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian (bookworm)"
DISTRO_COMMENT="Stable release."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.17.3/debian-bookworm-aarch64-pd-v4.17.3.tar.xz"
TARBALL_SHA256['aarch64']="3a841a794ae5999b33e33b329582ed0379d4f54ca62c6ce5a8eb9cff5ef8900b"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.17.3/debian-bookworm-arm-pd-v4.17.3.tar.xz"
TARBALL_SHA256['arm']="85861ab139d4042302796cf46a93a9efbcb4808c06f7a1ae5fb71812f4564424"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.17.3/debian-bookworm-i686-pd-v4.17.3.tar.xz"
TARBALL_SHA256['i686']="1fb3a6b0ea679e3797b35984049abf22bfe3b6ab79e9bb98cdfc54994712e1e4"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.17.3/debian-bookworm-x86_64-pd-v4.17.3.tar.xz"
TARBALL_SHA256['x86_64']="675e534333adcbf369e97abda3088927651e5d91612ae5727c52ff2284f4b8c8"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}

