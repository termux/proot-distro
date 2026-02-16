# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Manjaro"
DISTRO_COMMENT="Manjaro ARM64 port."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/manjaro-aarch64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['aarch64']="90fd86130d440b6d6ed6408b21306189eb41fe07d0026aab836ae203a1c419a4"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
