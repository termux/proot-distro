# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu"
DISTRO_COMMENT="Standard release (mantic). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/ubuntu-aarch64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['aarch64']="18f4746d56d8d9d223690706febcd45bef607d6240f4d137bc80d9d42f5d764a"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.6.0/ubuntu-arm-pd-v4.6.0.tar.xz"
TARBALL_SHA256['arm']="a37d63ba774c6d92ec54657261a9fc38b3b904a0e23aba70e1f44eae069a1c15"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/ubuntu-x86_64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['x86_64']="fc8bd25316640c12697c3960c3629dc824d725332fb5559b7c5a90b86fe5c269"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
