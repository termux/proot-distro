# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Manjaro"
DISTRO_COMMENT="Manjaro ARM64 port."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.18.0/manjaro-aarch64-pd-v4.18.0.tar.xz"
TARBALL_SHA256['aarch64']="f0aa5f70a4ccfe00f658bf9adc4a18d8332e894591839adb990d913c9aa604b9"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
