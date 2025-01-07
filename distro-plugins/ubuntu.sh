# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (24.04)"
DISTRO_COMMENT="LTS release (noble)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.17.3/ubuntu-noble-aarch64-pd-v4.17.3.tar.xz"
TARBALL_SHA256['aarch64']="81ac0fb0d16ded12ab11cede62f67b875ff56f9fa1aa9eb786415c3ec5c477d2"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.17.3/ubuntu-noble-arm-pd-v4.17.3.tar.xz"
TARBALL_SHA256['arm']="611f39e8b942202d14608026ef3d674b35a1fea6e780dbaa5ca001cbb63d04c0"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.17.3/ubuntu-noble-x86_64-pd-v4.17.3.tar.xz"
TARBALL_SHA256['x86_64']="1680d024bf47d2414a36759af528ca3569a73b70682daa3d9693ba63157cb5a8"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
