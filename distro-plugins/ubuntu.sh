# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu"
DISTRO_COMMENT="LTS release (mantic). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-mantic-aarch64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['aarch64']="34880b7dd5a015f71569e91fef743f426164fbe3b8da50a6f1ef8e3a4f6ec784"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-mantic-arm-pd-v4.7.0.tar.xz"
TARBALL_SHA256['arm']="1c4140f2c8af9005bbca804410cbe12500b0200edf4982bf680f78717780fdb4"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/ubuntu-mantic-x86_64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['x86_64']="5e3d9b3cf444a76e00f672aa1ed4710ea8fcc62ddf5dc3e1beb1493ee3376a70"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
