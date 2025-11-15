# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 43. Broken on Android 15+."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.31.0/fedora-aarch64-pd-v4.31.0.tar.xz"
TARBALL_SHA256['aarch64']="e3c0aca71572ed343a29c6f41c6150583bf840f903047bb97b1ec45cefe95865"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.31.0/fedora-x86_64-pd-v4.31.0.tar.xz"
TARBALL_SHA256['x86_64']="f8d43b3b82be1131a1a61e7bb2bfe7170a232e643855ee4fe3ac07b4c110ab97"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
