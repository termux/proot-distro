# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="OpenSUSE"
DISTRO_COMMENT="Leap release (16.0). No support for ARM and x86 32bit."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/opensuse-aarch64-pd-v4.34.2.tar.xz"
TARBALL_SHA256['aarch64']="ac161ca81deb7a0418f382d66c2110802b90481cfe0f925b02b69a5642674379"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/opensuse-x86_64-pd-v4.34.2.tar.xz"
TARBALL_SHA256['x86_64']="6e817d9f188f4a3a173b7a928b45c0b250189ba87c2eb74a953235c8c14a7f58"

distro_setup() {
	# Lock package filesystem to remove issues regarding zypper dup
	run_proot_cmd zypper al filesystem
}
