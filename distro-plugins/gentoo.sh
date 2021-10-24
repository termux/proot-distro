# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Gentoo"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v2.4.0/gentoo-aarch64-pd-v2.4.0.tar.xz"
TARBALL_SHA256['aarch64']="ec4588f8083a55ac3d855b7a33ab77f09dded65cddceeac2c1170526a49f27f8"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v2.4.0/gentoo-arm-pd-v2.4.0.tar.xz"
TARBALL_SHA256['arm']="c565e76341ef5988412fb9c870589cd109f8f3701781a7241407970132814725"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v2.4.0/gentoo-i686-pd-v2.4.0.tar.xz"
TARBALL_SHA256['i686']="03f05d1a199144be7df5680fbb84d34f58ebad104020f9d1a33df481d33ec946"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v2.4.0/gentoo-x86_64-pd-v2.4.0.tar.xz"
TARBALL_SHA256['x86_64']="9bdefd5dd3eed63ace9416de9c75b3c9d0ca5d3f37abc0fb23af1cba50e774f3"

distro_setup() {
        if [ "$DISTRO_ARCH" = "aarch64" ]; then
	  run_proot_cmd curl -LO http://distfiles.gentoo.org/experimental/prefix/arm/prefix-stage3-arm64-latest.tar.xz
	  run_proot_cmd tar -C /data/data/com.termux/files/home -xpf prefix-stage3-arm64-latest.tar.xz
            run_proot_cmd rm -f /usr/bin/patch
            run_proot_cmd mv /data/data/com.termux/files/home/gentoo64/usr/bin/patch /usr/bin/patch
            run_proot_cmd echo "USE+=\" -xattr\"" >> /etc/portage/make.conf
            run_proot_cmd emerge -v1 patch
            rm -rf /data/data/com.termux/files/home/gentoo64
        elif [ "$DISTRO_ARCH" = "arm" ]; then
	  run_proot_cmd curl -LO http://distfiles.gentoo.org/experimental/prefix/arm/prefix-stage3-armv7a_hardfp-latest.tar.xz
	  run_proot_cmd tar -C /data/data/com.termux/files/home -xpf prefix-stage3-armv7a_hardfp-latest.tar.xz
            run_proot_cmd rm -f /usr/bin/patch
            run_proot_cmd mv /data/data/com.termux/files/home/gentoo/usr/bin/patch /usr/bin/patch
            run_proot_cmd echo "USE+=\" -xattr\"" >> /etc/portage/make.conf
            run_proot_cmd emerge -v1 patch
            rm -rf /data/data/com.termux/files/home/gentoo
        fi
}
