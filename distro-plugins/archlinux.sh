# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="This is Arch Linux ARM project, not original Arch."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/archlinux-aarch64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['aarch64']="7e87d551845aedae5a111d1fdcc2f5a69b0805f365244f3fab3fe67cd4114f00"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.6.0/archlinux-arm-pd-v4.6.0.tar.xz"
TARBALL_SHA256['arm']="9edc60150ffdeae42b05fdcffdf06226641c442673f66b64af369504abe83a4b"

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
