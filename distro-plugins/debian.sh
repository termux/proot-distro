# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Debian (bookworm)"
DISTRO_COMMENT="Stable release."

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/debian-bookworm-aarch64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['aarch64']="4baa32280cc70b67e2c650777c1d974349f0cdf23afaabc305ad3bc6182b8df8"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.7.0/debian-bookworm-arm-pd-v4.7.0.tar.xz"
TARBALL_SHA256['arm']="0eba2cb93261d6e73c2f3c32ed7ebe9de408ceef584c5e0c0b7e237d294f7a8d"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.7.0/debian-bookworm-i686-pd-v4.7.0.tar.xz"
TARBALL_SHA256['i686']="7425f5fe7f34c718428f235b9155adb782c29ce6347f704f4a93a9da195b9aa3"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.7.0/debian-bookworm-x86_64-pd-v4.7.0.tar.xz"
TARBALL_SHA256['x86_64']="164932ab77a0b94a8e355c9b68158a5b76d5abef89ada509488c44ff54655d61"

distro_setup() {
	# Configure en_US.UTF-8 locale.
	sed -i -E 's/#[[:space:]]?(en_US.UTF-8[[:space:]]+UTF-8)/\1/g' ./etc/locale.gen
	run_proot_cmd DEBIAN_FRONTEND=noninteractive dpkg-reconfigure locales
}

