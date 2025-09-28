# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 42. Broken on Android 15+."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/fedora-aarch64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['aarch64']="0f683e7c3b250660b490479f0c1c57e7450082f6ddc81375ddc863e611a76c65"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/fedora-x86_64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['x86_64']="68ce058124b2c412a02189a10dfa89e703300f5d6560dcdce1ae9506894872ab"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
