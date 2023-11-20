# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian"
DISTRO_COMMENT="A stable release (bookworm)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.0.2/debian-aarch64-pd-v4.0.2.tar.xz"
TARBALL_SHA256['aarch64']="5100c435d4f410e4bce0d448e273a3783a9a94e31bfb86f1e1448f7e696df87a"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.0.2/debian-arm-pd-v4.0.2.tar.xz"
TARBALL_SHA256['arm']="5fd0cc8a3b96486658222569a8d250f1ed1ae3bc2c54cff4c191b7625264ab1a"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.0.2/debian-i686-pd-v4.0.2.tar.xz"
TARBALL_SHA256['i686']="6a3cdbf0a19251f932d256896ede242dddb2fec0e669f5cabcb1d21942745a0d"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.0.2/debian-x86_64-pd-v4.0.2.tar.xz"
TARBALL_SHA256['x86_64']="ed26084174fc6c000817276f3fa36594327c5df2f8f7ba98007474d64857b669"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	run_proot_cmd sed -i -E 's/# (en_US.UTF-8)/\1/g' /etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}

