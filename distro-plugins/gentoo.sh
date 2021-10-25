# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Gentoo"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v2.6.9/gentoo-aarch64-pd-v2.6.9.tar.xz"
TARBALL_SHA256['aarch64']="674b9c1b3a012ff28dbfe195a634ad4539ca79bc34eca358d1224d1ddf44f49f"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v2.6.9/gentoo-arm-pd-v2.6.9.tar.xz"
TARBALL_SHA256['arm']="8eb11916a86760d37ce5169e3ddf4a9526827014df23ed120cf30ccabedbc505"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v2.6.9/gentoo-i686-pd-v2.6.9.tar.xz"
TARBALL_SHA256['i686']="f62a49e5004f50ef404aa74fe257c17ab22c30e07d80dddd64747e295ed9e4b3"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v2.6.9/gentoo-x86_64-pd-v2.6.9.tar.xz"
TARBALL_SHA256['x86_64']="9bb5aa0f862356b1f02d1b8ce717e8149deb07bf6b144599fe0805dff13de216"

distro_setup() {
	if [ "$DISTRO_ARCH" = "aarch64" ]; then
		run_proot_cmd curl --fail --location --output /gentoo-prefix.tar.xz 			https://distfiles.gentoo.org/experimental/prefix/arm/prefix-stage3-arm64-latest.tar.xz
		run_proot_cmd tar -C / -xvpf /gentoo-prefix.tar.xz --strip-components=1 gentoo64/usr/bin/patch
		run_proot_cmd rm -f /gentoo-prefix.tar.xz
		run_proot_cmd bash -c 'echo "USE=\"-xattr\"" >> /etc/portage/make.conf'
		run_proot_cmd emerge-webrsync
		run_proot_cmd emerge -v1 patch
	elif [ "$DISTRO_ARCH" = "arm" ]; then
		run_proot_cmd curl --fail --location --output /gentoo-prefix.tar.xz 			https://distfiles.gentoo.org/experimental/prefix/arm/prefix-stage3-armv7a_hardfp-latest.tar.xz
		run_proot_cmd tar -C / -xvpf /gentoo-prefix.tar.xz --strip-components=1 gentoo/usr/bin/patch
		run_proot_cmd rm -f /gentoo-prefix.tar.xz
		run_proot_cmd bash -c 'echo "USE=\"-xattr\"" >> /etc/portage/make.conf'
		run_proot_cmd emerge-webrsync
		run_proot_cmd emerge -v1 patch
	fi
}
