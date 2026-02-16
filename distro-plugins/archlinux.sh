# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="ARM(64) devices use Arch Linux ARM, i686 uses Arch Linux 32. Both are independent projects. The original Arch usable only by x86_64 devices."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/archlinux-aarch64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['aarch64']="718151cc4adad701223c689a7e4690cb7710b7b16e9b23617b671856ff04d563"
TARBALL_URL['arm']="https://easycli.sh/proot-distro/archlinux-arm-pd-v4.37.0.tar.xz"
TARBALL_SHA256['arm']="abc5d7d135db40a9e27a724553101b6ea13341e084cbb8b1d38befd9088f88bc"
TARBALL_URL['i686']="https://easycli.sh/proot-distro/archlinux-i686-pd-v4.37.0.tar.xz"
TARBALL_SHA256['i686']="7997c0f1a294585f571a4adf619690762130dfee0b43333458c763270666e979"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/archlinux-x86_64-pd-v4.37.0.tar.xz"
TARBALL_SHA256['x86_64']="ebff09d2603f25205f1d8a2bd05b132fde571dd32f2ee58638f3a8dd8735282d"

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
