# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="openSUSE"
DISTRO_COMMENT="Rolling release (Tumbleweed)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/opensuse-aarch64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['aarch64']="cf81e80a7116f1a4638ba37619c4daf9b1052cc9635fe2925b26b9b60508600d"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.6.0/opensuse-arm-pd-v4.6.0.tar.xz"
TARBALL_SHA256['arm']="e191db4043949f550585fc9fa2912229384a64b61976ee937364a460dc826b68"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.6.0/opensuse-i686-pd-v4.6.0.tar.xz"
TARBALL_SHA256['i686']="e497ebea3d92c676a320f48ed35146d564160791dbfa7bc677f0959bb3a40248"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/opensuse-x86_64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['x86_64']="8cdfc2965983cfae03ae05f46eb68771cb7f19699c85bd9fb9f453cee5c7cb50"

distro_setup() {
	# Lock package filesystem to remove issues regarding zypper dup
	run_proot_cmd zypper al filesystem
}
