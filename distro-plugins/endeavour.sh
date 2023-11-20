# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="EndeavourOS"
DISTRO_COMMENT="Currently available only for AArch64."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.0.2/endeavouros-aarch64-pd-v4.0.2.tar.xz"
TARBALL_SHA256['aarch64']="20da9dc7528fc168bd69d4c5f84dfa3b25e8cc16a6c2d3badcb649b255ed2945"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
