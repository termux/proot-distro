# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Arch Linux"
DISTRO_COMMENT="Currently available only AArch64 and ARM ports."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.10.0/archlinux-aarch64-pd-v3.10.0.tar.xz"
TARBALL_SHA256['aarch64']="ffad3d535bf5172fe379fd68de4353e0951cd3e67b255cec6440e962a7d2bb4f"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.10.0/archlinux-arm-pd-v3.10.0.tar.xz"
TARBALL_SHA256['arm']="5643e2835061d93bfe6de13de607bebd33dd75eb97b32866ac5c29dff37521dd"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v3.10.0/archlinux-i686-pd-v3.10.0.tar.xz"
TARBALL_SHA256['i686']=""
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.10.0/archlinux-x86_64-pd-v3.10.0.tar.xz"
TARBALL_SHA256['x86_64']=""

distro_setup() {
	# Fix environment variables on login or su.
	local f
	for f in su su-l system-local-login system-remote-login; do
		echo "session  required  pam_env.so readenv=1" >> ./etc/pam.d/"${f}"
	done
}
