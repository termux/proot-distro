# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Artix Linux"

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/artix-aarch64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['aarch64']="fe499e00903db5342969ea2d87a97349c78b43e4cb53f0388cec5ad8cc35e92c"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
