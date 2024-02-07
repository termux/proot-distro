# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (23.04 LTS)"
DISTRO_COMMENT="LTS release (jammy). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.8.0/ubuntu-jammy-aarch64-pd-v4.8.0.tar.xz"
TARBALL_SHA256['aarch64']="6517259b712de5429ebb630e537f2ff2d1d4ae2a940f9179e26d91a4b723c16c"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.8.0/ubuntu-jammy-arm-pd-v4.8.0.tar.xz"
TARBALL_SHA256['arm']="1efa6ab1c35194339d1788bba9f5d570f29f27c12e70d46ed14d9f516b918563"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.8.0/ubuntu-jammy-x86_64-pd-v4.8.0.tar.xz"
TARBALL_SHA256['x86_64']="07b0c89897348f4312ce087e521eadf7937c939ee41f02a444caaff37cdd3407"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
