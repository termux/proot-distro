# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="OpenSUSE"
DISTRO_COMMENT="Rolling release (Tumbleweed)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.18.0/opensuse-aarch64-pd-v4.18.0.tar.xz"
TARBALL_SHA256['aarch64']="af234643447fd86ac90a9e380501f202eb131046cc6aa3a97807b40bdafcfcf2"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.18.0/opensuse-arm-pd-v4.18.0.tar.xz"
TARBALL_SHA256['arm']="1c569c685626789856786147263043cadc29380d94f11d7d837b8d569083d91b"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.18.0/opensuse-i686-pd-v4.18.0.tar.xz"
TARBALL_SHA256['i686']="95dee97a4032b424b3445823f0bcf985d792d3ae56d525a902c8dd72589553a3"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.18.0/opensuse-x86_64-pd-v4.18.0.tar.xz"
TARBALL_SHA256['x86_64']="9b3f8e452cf20f23dffd424c4d55942a1fb40657e98c2e2fa50ac7af59e4c9bd"

distro_setup() {
	# Lock package filesystem to remove issues regarding zypper dup
	run_proot_cmd zypper al filesystem
}
