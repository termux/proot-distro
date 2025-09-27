# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Manjaro"
DISTRO_COMMENT="Manjaro ARM64 port."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/manjaro-aarch64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['aarch64']="2847ef8817cfddc124b0af7b5cbe31474df8b4b317c6b3dfabf1b8a19d270e70"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
