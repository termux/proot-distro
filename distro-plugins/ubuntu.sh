# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (23.10)"
DISTRO_COMMENT="Regular release (mantic). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.8.0/ubuntu-mantic-aarch64-pd-v4.8.0.tar.xz"
TARBALL_SHA256['aarch64']="1057ec14045fa2174e0c5a5249db59bb1206573f273c0c5ae0bcfc877fe732df"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.8.0/ubuntu-mantic-arm-pd-v4.8.0.tar.xz"
TARBALL_SHA256['arm']="eb968b49e61892d8f02fcee88e130169e737838a8f94f9464e58b2c9cd84e003"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.8.0/ubuntu-mantic-x86_64-pd-v4.8.0.tar.xz"
TARBALL_SHA256['x86_64']="b621afb46a182ce2aa06d06b2d3bc077bbdf08b3557b892d5ca74ff1c9afc206"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
