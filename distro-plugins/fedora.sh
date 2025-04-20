# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 42."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.24.0/fedora-aarch64-pd-v4.24.0.tar.xz"
TARBALL_SHA256['aarch64']="48abf1d8b9cc7625d4212cc604ce3c113ea6d6d806de60b2c3f74c5b5452cd72"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.24.0/fedora-x86_64-pd-v4.24.0.tar.xz"
TARBALL_SHA256['x86_64']="105ffd9a7d989ac09ee3adb7c4f00b72a3cc997c1e3fd99599bf16578dd8e20c"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
