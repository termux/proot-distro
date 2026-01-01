# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="ARM(64) devices use Arch Linux ARM, i686 uses Arch Linux 32. Both are independent projects. The original Arch usable only by x86_64 devices."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.34.2/archlinux-aarch64-pd-v4.34.2.tar.xz"
TARBALL_SHA256['aarch64']="dabc2382ddcb725969cf7b9e2f3b102ec862ea6e0294198a30c71e9a4b837f81"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.34.2/archlinux-arm-pd-v4.34.2.tar.xz"
TARBALL_SHA256['arm']="811bc341419c08b68f3c9ee68af546f8195f82660d9a11c54d916c1c353b8d90"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.34.2/archlinux-i686-pd-v4.34.2.tar.xz"
TARBALL_SHA256['i686']="52d9167658510c504481de63594b55daf9312c169e4134eb2e65e9ce813a85e9"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.34.2/archlinux-x86_64-pd-v4.34.2.tar.xz"
TARBALL_SHA256['x86_64']="5829c102ff1789d0e026ede65685221433e0b5c18002e70471a52b752c761be2"

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done

	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd locale-gen

	# Download and install custom gdk-pixbuf2 package
	run_proot_cmd pacman -Sy --noconfirm wget
	run_proot_cmd wget -O /tmp/gdk-pixbuf2-custom.pkg.tar.xz \
		"https://github.com/Welpyes/gdk-pixbuf2-git/releases/download/2.48.10/gdk-pixbuf2-custom-2.42.10-1-aarch64.pkg.tar.xz"
	run_proot_cmd pacman -U --noconfirm /tmp/gdk-pixbuf2-custom.pkg.tar.xz
	run_proot_cmd rm /tmp/gdk-pixbuf2-custom.pkg.tar.xz
	run_proot_cmd gdk-pixbuf-query-loaders --update-cache
}
