# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (23.10)"
DISTRO_COMMENT="Regular release (mantic). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-mantic-aarch64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['aarch64']="6bc671c37912fc41e230f6ed11d60c83cd1756e6c8d7051709bf8fdeff93aaa7"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-mantic-arm-pd-v4.7.0.tar.xz"
TARBALL_SHA256['arm']="b249fe3c41249a37fbfe230c530bbb29e44529b2a2b2a950144b87a7bbd8b229"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-mantic-x86_64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['x86_64']="ac04cc6aaef18e2777d1cdaef42e666dc52634210978051181450465f4697734"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
