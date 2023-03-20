# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="OpenSUSE"
DISTRO_COMMENT="Rolling release (Tumbleweed)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.5.1/opensuse-aarch64-pd-v3.5.1.tar.xz"
TARBALL_SHA256['aarch64']="4553766c90428329e40e1fe68264e8b508861bd4f7125f6f3493046feac47330"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.5.1/opensuse-arm-pd-v3.5.1.tar.xz"
TARBALL_SHA256['arm']="74643fffeff3922fcbb46df3fce72a3df7b6ef4eb6ae107d592c24ecfbb56301"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v3.5.1/opensuse-i686-pd-v3.5.1.tar.xz"
TARBALL_SHA256['i686']="011876851125ebed1f1c8b56151e61e0ce603b6ea8f517dcd9784d9939234875"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.5.1/opensuse-x86_64-pd-v3.5.1.tar.xz"
TARBALL_SHA256['x86_64']="8d9d09572bdb55d73ba09f915d7fedc8656b9c44bcccd33b2eaef0466e602878"

distro_setup() {
	# Lock package filesystem to remove issues regarding zypper dup
	zypper al filesystem
}
