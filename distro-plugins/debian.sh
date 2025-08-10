# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian (trixie)"
DISTRO_COMMENT="Stable release."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.26.0/debian-trixie-aarch64-pd-v4.26.0.tar.xz"
TARBALL_SHA256['aarch64']="cda75346f2c9e09e8a802665745b5a7e2bd6d8584dbf1c86c8c57ef54c4e2d3c"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.26.0/debian-trixie-arm-pd-v4.26.0.tar.xz"
TARBALL_SHA256['arm']="868ad59b44098d7175819bdabda0dcd98ddc67af15c153cc9e22797ac77f9dd3"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.26.0/debian-trixie-i686-pd-v4.26.0.tar.xz"
TARBALL_SHA256['i686']="8579087c23d759f3ded2c88d8eb707fe9efe524330c8894828f3246cdcc36117"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.26.0/debian-trixie-x86_64-pd-v4.26.0.tar.xz"
TARBALL_SHA256['x86_64']="e2edc15363395936cf0cba8c440a108458dba58fb496d3d962909d7a8d9777ae"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}

