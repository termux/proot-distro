# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Fedora"
DISTRO_COMMENT="Version 38. Supports only 64-bit CPUs."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.15.2/fedora-aarch64-pd-v3.15.2.tar.xz"
TARBALL_SHA256['aarch64']="d6aa1a51f8d1f11a3388ed32a30643410345d5c8d22cb7c33e36cfa60942bbb1"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.15.2/fedora-x86_64-pd-v3.15.2.tar.xz"
TARBALL_SHA256['x86_64']="167732ad9389523ca88ca9fdba470413322be15b3110845fa865e17b81e3ffaa"

distro_setup() {
	# Fix environment variables on login or su.
	run_proot_cmd authselect opt-out
	echo "session  required  pam_env.so readenv=1" | run_proot_cmd tee -a /etc/pam.d/system-auth >/dev/null
}
