# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu"
DISTRO_COMMENT="Standard release (mantic). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.0.2/ubuntu-aarch64-pd-v4.0.2.tar.xz"
TARBALL_SHA256['aarch64']="257e71bbbb8f336491f63a1d1927a83584d8b4ff8a7f4fb15392674473b838d2"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.0.2/ubuntu-arm-pd-v4.0.2.tar.xz"
TARBALL_SHA256['arm']="aa72f2a1bbb9d55e9b6b239d539183990e8ba6b2fcd038f5cb5680e6326b17b6"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.0.2/ubuntu-x86_64-pd-v4.0.2.tar.xz"
TARBALL_SHA256['x86_64']="c9e5e35dafc3dfaf915259025f1f1c58aad1fce9c45396e2f4da44d578c4be19"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	run_proot_cmd sed -i -E 's/# (en_US.UTF-8)/\1/g' /etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
