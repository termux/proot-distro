# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Artix Linux"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/artix-aarch64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['aarch64']="7963559a4e7f610d80823cce60743704fae61a8fce42d2b86601ae3dd28ce292"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
