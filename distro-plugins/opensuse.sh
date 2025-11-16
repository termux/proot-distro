# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="OpenSUSE"
DISTRO_COMMENT="Leap release (16.0). No support for ARM and x86 32bit."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.31.0/opensuse-aarch64-pd-v4.31.0.tar.xz"
TARBALL_SHA256['aarch64']="8f5accdfb6404dc3caa0487505366a19c2a0cb742839353f862b583c8033128a"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.31.0/opensuse-arm-pd-v4.31.0.tar.xz"
TARBALL_SHA256['arm']=""
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.31.0/opensuse-i686-pd-v4.31.0.tar.xz"
TARBALL_SHA256['i686']=""
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.31.0/opensuse-x86_64-pd-v4.31.0.tar.xz"
TARBALL_SHA256['x86_64']="cc7008ce71993f4f5505a204769c7e6aca19f60da945c762b0639b7c9a00cdd5"

distro_setup() {
	# Lock package filesystem to remove issues regarding zypper dup
	run_proot_cmd zypper al filesystem
}
