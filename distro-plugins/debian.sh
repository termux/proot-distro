# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian"
DISTRO_COMMENT="A stable release (bookworm)."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/debian-aarch64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['aarch64']="68dab31b46af61114014b54876c4f317be648ce8c76c0c6cbb5d6011d420886c"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.6.0/debian-arm-pd-v4.6.0.tar.xz"
TARBALL_SHA256['arm']="8298f99afef34b135bc86025d65d638a234068ede00bf2e93f6cc1e1dcfc0196"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.6.0/debian-i686-pd-v4.6.0.tar.xz"
TARBALL_SHA256['i686']="beb475580f74ed64b784602b27755e4178ed360a84f64e2bbeaf8372cb60ecdf"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.6.0/debian-x86_64-pd-v4.6.0.tar.xz"
TARBALL_SHA256['x86_64']="1cdf67f0d458d6109e527415691db7b27b9d374a29b17226cdd2d9f1aa7660ef"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}

