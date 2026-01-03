# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Trisquel GNU/Linux"
DISTRO_COMMENT="Version 'aramo'."

TARBALL_URL['aarch64']="https://easycli.sh/proot-distro/trisquel-aarch64-pd-v4.31.0.tar.xz"
TARBALL_SHA256['aarch64']="7a6bc54c0a66f16d2ad8ff58730bbd689766b99eb114f56ff260939490ae5446"
TARBALL_URL['arm']="https://easycli.sh/proot-distro/trisquel-arm-pd-v4.31.0.tar.xz"
TARBALL_SHA256['arm']="0da21cc6c06aa53f89495d71151da69e1c5bb92af974b7dda8de4b1b7df28863"
TARBALL_URL['i686']="https://easycli.sh/proot-distro/trisquel-i686-pd-v4.31.0.tar.xz"
TARBALL_SHA256['i686']="cf45df57beb015178d588463bf2c6a686952930a9a3f4583fc9bcc2e54e1412b"
TARBALL_URL['x86_64']="https://easycli.sh/proot-distro/trisquel-x86_64-pd-v4.31.0.tar.xz"
TARBALL_SHA256['x86_64']="834fe6ba50a448e9bbab1556002e66b81e7712ffa44b961678fb6de10136de17"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}
