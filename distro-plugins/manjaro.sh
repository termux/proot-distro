# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Manjaro"
DISTRO_COMMENT="Currently available only for AArch64."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.18.1/manjaro-aarch64-pd-v3.18.1.tar.xz"
TARBALL_SHA256['aarch64']="6d6595ab9650a1836efe9dd23e0b7b321cdaf01b15ed95413036f53f6a25f495"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
