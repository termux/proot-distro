# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 39. Supports only 64-bit CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.0.2/fedora-aarch64-pd-v4.0.2.tar.xz"
TARBALL_SHA256['aarch64']="339777a5ab14212b7541d1289aef33540c88a9a247035ba05144bd8e58903b84"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.0.2/fedora-x86_64-pd-v4.0.2.tar.xz"
TARBALL_SHA256['x86_64']="b23bd177486b27b9252f93c8a421e9d0092219fe3efeb3536f170e97d6dc604c"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/system-auth
}
