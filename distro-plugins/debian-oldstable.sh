# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian (bullseye)"
DISTRO_COMMENT="Old stable release."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/debian-bullseye-aarch64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['aarch64']="8dc2cb6e8ba19518ffdc5c5f6d3d7a1f7a898ebabd49b8fab0fc59d67e305754"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.7.0/debian-bullseye-arm-pd-v4.7.0.tar.xz"
TARBALL_SHA256['arm']="3151f1323ae555a03a43e3ce4605de29df87bf77645bef0ec7c806353f71c6ee"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.7.0/debian-bullseye-i686-pd-v4.7.0.tar.xz"
TARBALL_SHA256['i686']="c38f0d2736c6cdf9b33d7ebd904b3d9218a55bad83030dc447925bec10c0bd6c"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/debian-bullseye-x86_64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['x86_64']="46af4f9ed6db581e3ecedb57955a27c530282eac5ec2aa0c9e525c37c117d5bd"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}

