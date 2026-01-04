# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="ARM(64) devices use Arch Linux ARM, i686 uses Arch Linux 32. Both are independent projects. The original Arch usable only by x86_64 devices."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/archlinux-aarch64-pd-v4.36.0.tar.xz"
TARBALL_SHA256['aarch64']="f4318f971e62a5e8407191728b3b92414bcb0f82267774d09d8e25de68acb650"
TARBALL_URL['arm']="https://easycli.sh/proot-distro/archlinux-arm-pd-v4.36.0.tar.xz"
TARBALL_SHA256['arm']="8390cf5ecc206556f57f2534a0a00c6e0e5c4ee34a77bf4cc6c981ef120556db"
TARBALL_URL['i686']="https://easycli.sh/proot-distro/archlinux-i686-pd-v4.36.0.tar.xz"
TARBALL_SHA256['i686']="589ded6f640e7900534ac25aaa0f7ea7e9a4bae9d98b27c5c34ae30b78e5d6e4"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/archlinux-x86_64-pd-v4.36.0.tar.xz"
TARBALL_SHA256['x86_64']="7881d975bf07e1803721c780275eaa5ebd8a06da0dd6cbfca49ee625b94a39dc"

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
