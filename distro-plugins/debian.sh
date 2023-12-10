# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian"
DISTRO_COMMENT="A stable release (bookworm)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.5.0/debian-aarch64-pd-v4.5.0.tar.xz"
TARBALL_SHA256['aarch64']="bd675876f420c1c774649360041d7c4c268dc37d587afedfa6abe37580088cfa"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.5.0/debian-arm-pd-v4.5.0.tar.xz"
TARBALL_SHA256['arm']="579cd4b32eea89729e9231783a919df667fe28606c413b7550889c8c8968fa41"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.5.0/debian-i686-pd-v4.5.0.tar.xz"
TARBALL_SHA256['i686']="8309a33dea0c2002154aa9b62845ed376ac443fdc73c0f979dfc5d657a90c3df"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.5.0/debian-x86_64-pd-v4.5.0.tar.xz"
TARBALL_SHA256['x86_64']="c5320c114a1e9d4caa86642f9b9f4c3933788f0382250cfbad9e03bd57a9337d"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}

