# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 41."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.23.0/fedora-aarch64-pd-v4.23.0.tar.xz"
TARBALL_SHA256['aarch64']="837ccdbd862c96bf7140c0c21f32f2b89b7c910cb5760e6d5946c425c640524c"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.23.0/fedora-x86_64-pd-v4.23.0.tar.xz"
TARBALL_SHA256['x86_64']="db2fd4267fa38b1dc10178b8e9e8e0f6d5a100360b8e2ba2d7fd4916225619fd"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
