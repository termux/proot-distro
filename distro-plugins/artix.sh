# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Artix Linux"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/artix-aarch64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['aarch64']="c6ef7e1cbf8dd88755b8b2c681a7830c7a79476dd47d7a43d2a7fe537c1d5b28"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
