# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="Currently available only AArch64 and ARM ports."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.5.0/archlinux-aarch64-pd-v4.5.0.tar.xz"
TARBALL_SHA256['aarch64']="fd4756ae5ecad0bf7bbd8eb8d3d76cd013ee74a0a41f4f92ec082aba5f346269"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.5.0/archlinux-arm-pd-v4.5.0.tar.xz"
TARBALL_SHA256['arm']="73c3ee3b5fbe31fffa39587ad501862e7a173304ab235eb90ee9712967e6ce4b"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done

	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd locale-gen
}
