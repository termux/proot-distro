# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="OpenSUSE"
DISTRO_COMMENT="Leap release (15.6). No support for ARM and x86 32bit."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/opensuse-aarch64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['aarch64']="ef16b5c6d1c9abe86c36d752ff2b4617717b7d995fdb6fff04a1150a1fbbf279"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.29.0/opensuse-arm-pd-v4.29.0.tar.xz"
TARBALL_SHA256['arm']=""
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.29.0/opensuse-i686-pd-v4.29.0.tar.xz"
TARBALL_SHA256['i686']=""
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/opensuse-x86_64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['x86_64']="e328b69edf4e7427d5b60d6c1a46a05ebd004e993fb7b694d136a081b1485a8f"

distro_setup() {
	# Lock package filesystem to remove issues regarding zypper dup
	run_proot_cmd zypper al filesystem
}
