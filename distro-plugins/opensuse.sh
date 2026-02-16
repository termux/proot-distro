# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="OpenSUSE"
DISTRO_COMMENT="Leap release (16.0). No support for ARM and x86 32bit."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/opensuse-aarch64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['aarch64']="812bbed638f43b81846520bf4283c18da08e19f14714e56fffdc9ccad3c65d7a"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/opensuse-x86_64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['x86_64']="56cd4b5bb298da2ad25d66ec5f180c0f577c7f70358f323c62c318f8b8530ff7"

distro_setup() {
	# Lock package filesystem to remove issues regarding zypper dup
	run_proot_cmd zypper al filesystem
}
