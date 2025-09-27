# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="ARM(64) devices use Arch Linux ARM, i686 uses Arch Linux 32. Both are independent projects. The original Arch usable only by x86_64 devices."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/archlinux-aarch64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['aarch64']="08d74365213e647c558e561b0a2a7afb6fa3dfe345a1994c62ccac5af1a1cdc6"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.29.0/archlinux-arm-pd-v4.29.0.tar.xz"
TARBALL_SHA256['arm']="df17fd1058a103ed64811900498c9432abd303eee3eb27cbacab041a14011fba"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.29.0/archlinux-i686-pd-v4.29.0.tar.xz"
TARBALL_SHA256['i686']="5fc6240f81c88bb69391c189dd2d0f4f5d9dc9503b400baee3bef060a49ee37c"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.29.0/archlinux-x86_64-pd-v4.29.0.tar.xz"
TARBALL_SHA256['x86_64']="8249202836643a4a4f922004c34faa2c3f7d9fec0464ee23b087ad325f1610d9"

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
