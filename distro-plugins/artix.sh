# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Artix Linux"
DISTRO_COMMENT="Currently available only for AArch64."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.18.1/artix-aarch64-pd-v3.18.1.tar.xz"
TARBALL_SHA256['aarch64']="9801031864da6bc9dc69293695797f8aba7866c273bb7403f95c4e70be987936"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
