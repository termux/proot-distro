# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu"
DISTRO_COMMENT="LTS release (lunar). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-lunar-aarch64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['aarch64']="4fa21d6a72bc687492a2d11270d5999cf2e4741cfbbda8707b36c242bfc993ef"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-lunar-arm-pd-v4.7.0.tar.xz"
TARBALL_SHA256['arm']="4324fe1acdb964793c1c3091897e4ec68d9d0d84687b1fe77fc3662594d17c22"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-lunar-x86_64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['x86_64']="20963528868d2518078a25e4baa5cb487ffe89bca4a1794674549b04d8ea6ac3"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
