# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="Currently available only AArch64 and ARM ports."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.18.1/archlinux-aarch64-pd-v3.18.1.tar.xz"
TARBALL_SHA256['aarch64']="68de6db105dc503e8defe55ac37fad9b531f07aa16b8a8072c505fff5fbc03a1"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.18.1/archlinux-arm-pd-v3.18.1.tar.xz"
TARBALL_SHA256['arm']="2701e2aac78bb0cb86f113701ae226c35b38a4e8f5404ae97e7eb0cc4599ab79"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
