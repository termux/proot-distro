# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Gentoo"
DISTRO_ARCH="aarch64"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v2.4.0/gentoo-aarch64-pd-v2.4.0.tar.xz"
TARBALL_SHA256['aarch64']="ec4588f8083a55ac3d855b7a33ab77f09dded65cddceeac2c1170526a49f27f8"

distro_setup() {
	run_proot_cmd curl -LO http://distfiles.gentoo.org/experimental/prefix/arm/prefix-stage3-arm64-latest.tar.xz
	run_proot_cmd tar -C /data -xf prefix-stage3-arm64-latest.tar.xz
        run_proot_cmd mv /usr/bin/patch /usr/bin/patch.bk
        run_proot_cmd rm -f /usr/bin/patch
        run_proot_cmd mv /data/gentoo64/usr/bin/patch /usr/bin/patch
        run_proot_cmd echo "USE+=\" -xattr\"" >> /etc/portage/make.conf
        run_proot_cmd emerge -v1 patch
        run_proot_cmd rm /usr/bin/patch.bk
        run_proot_cmd rm -rf /data/gentoo64
}
