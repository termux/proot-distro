# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu"
DISTRO_COMMENT="Standard release (mantic). Not available for x86 32-bit (i686) CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.5.0/ubuntu-aarch64-pd-v4.5.0.tar.xz"
TARBALL_SHA256['aarch64']="78f6e74531c6e7c61d6b90ea9b7e25738c14f2f4f2bb07cb1614414ef06017d5"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.5.0/ubuntu-arm-pd-v4.5.0.tar.xz"
TARBALL_SHA256['arm']="3af5171713631aa2f672602f0099d22e242a9fe19df910924dbf5e93497496c8"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.5.0/ubuntu-x86_64-pd-v4.5.0.tar.xz"
TARBALL_SHA256['x86_64']="5a5212f5389fb805030c4db2dcde99590136291bb9f44aad62b4665b66f66669"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
