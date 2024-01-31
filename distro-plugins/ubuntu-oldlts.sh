# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu"
DISTRO_COMMENT="Previous LTS release (jammy). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-jammy-aarch64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['aarch64']="d4e3aa02399f84806239c7cea491c5d75eaf8d9e8e00b9f0318d5b990a908519"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-jammy-arm-pd-v4.7.0.tar.xz"
TARBALL_SHA256['arm']="d2416a9c9df2017edc98d2bc9210c13abbb98a81fc27db5b319c5e1887a7cae5"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-jammy-x86_64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['x86_64']="98ac3ae3c273da0677e10501babf6e756c8a8b4165fe49c48225b772f102e33c"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
