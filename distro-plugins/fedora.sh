# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 42. Broken on Android 15+."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/fedora-aarch64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['aarch64']="9a339ceed8af6f6d71a44ae195ad68721774e1f07bf46fb24984238f58381654"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/fedora-x86_64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['x86_64']="ce5540b088ad847174dfbdb56076706b9078e2bcd0b8b8985615ebc66d566554"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
