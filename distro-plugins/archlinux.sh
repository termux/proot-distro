# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="ARM(64) devices use Arch Linux ARM, i686 uses Arch Linux 32. Both are independent projects. The original Arch usable only by x86_64 devices."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.22.1/archlinux-aarch64-pd-v4.22.1.tar.xz"
TARBALL_SHA256['aarch64']="b7e4cfb1414a281f90bfd39a503f72f38e03c31b356927972f797988fb48b5b1"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.22.1/archlinux-arm-pd-v4.22.1.tar.xz"
TARBALL_SHA256['arm']="25ccafc3234bc9e0cd37ea240a4b6ec349464e88cf22e9db0d11a9f1a927d336"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.22.1/archlinux-i686-pd-v4.22.1.tar.xz"
TARBALL_SHA256['i686']="eb9221cfb51b1f39da958b7a7ea6ea7ddfa66297d0cded18bb14591502a9d151"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.22.1/archlinux-x86_64-pd-v4.22.1.tar.xz"
TARBALL_SHA256['x86_64']="a0cf76c31c79e260766dee80657bd7683fa30dafe900952018264462c1728e17"

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
