# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 41."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.17.3/fedora-aarch64-pd-v4.17.3.tar.xz"
TARBALL_SHA256['aarch64']="5f51f3f2da790732fc6b720eefe5ec44841cf8edb21dfa342005257c4665bb8c"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.17.3/fedora-x86_64-pd-v4.17.3.tar.xz"
TARBALL_SHA256['x86_64']="857bd545ea1f3a4ad8fba907cabd8059abbd04abf29f5d543f473f7e051a3eb1"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
