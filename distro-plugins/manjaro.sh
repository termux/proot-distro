# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Manjaro"
DISTRO_COMMENT="Manjaro ARM64 port."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.34.2/manjaro-aarch64-pd-v4.34.2.tar.xz"
TARBALL_SHA256['aarch64']="9778ecfc6efb623a20b441b901da433d5703d570546245ee8179dcede2921544"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
