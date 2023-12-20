# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Manjaro"
DISTRO_COMMENT="Currently available only for AArch64."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/manjaro-aarch64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['aarch64']="54125f308bfe4cb30fedae290f4b276e00f331db89fdebb607cf120a5a72feb2"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
