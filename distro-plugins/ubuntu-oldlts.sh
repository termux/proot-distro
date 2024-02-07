# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (22.04 LTS)"
DISTRO_COMMENT="Previous LTS release (focal). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.8.0/ubuntu-focal-aarch64-pd-v4.8.0.tar.xz"
TARBALL_SHA256['aarch64']="386704baf86bebaf39141a92f4a5fa4cf4fdb94bcb55b85e94b1aea603e000ff"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.8.0/ubuntu-focal-arm-pd-v4.8.0.tar.xz"
TARBALL_SHA256['arm']="ee38e86e7306f6d61683d5ffd0902ae3aa90550d062aac3ee412eba4db0e66dd"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.8.0/ubuntu-focal-x86_64-pd-v4.8.0.tar.xz"
TARBALL_SHA256['x86_64']="9ced22fc16aec1de554fd1d6fa12e628a27a6db2f00924b43d8b32cbeb1f1454"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
