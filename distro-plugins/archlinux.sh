# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="ARM(64) devices use Arch Linux ARM, i686 uses Arch Linux 32. Both are independent projects. The original Arch usable only by x86_64 devices."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.17.3/archlinux-aarch64-pd-v4.17.3.tar.xz"
TARBALL_SHA256['aarch64']="dc56b998ffa2663209417396c8d70caf87c8052acf41e9a2c6daf24cbd181533"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.17.3/archlinux-arm-pd-v4.17.3.tar.xz"
TARBALL_SHA256['arm']="4b698018ded0656e17c0867b97b53cc32be5906c0f37e02ab499c65d5f12d439"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.17.3/archlinux-i686-pd-v4.17.3.tar.xz"
TARBALL_SHA256['i686']="787d43c21fae2c6efe843a324ff2875fc654fe8475020deb8678c224967f29af"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.17.3/archlinux-x86_64-pd-v4.17.3.tar.xz"
TARBALL_SHA256['x86_64']="75e069c3f59f4806848972dfd6a2d390b0328ca3f4486db140eb21d1d376b35b"

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
