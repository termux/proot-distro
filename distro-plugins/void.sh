# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Void Linux"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.5.1/void-aarch64-pd-v3.5.1.tar.xz"
TARBALL_SHA256['aarch64']="c52bb1bba2391382467d581ad277fd72e5d9d7a8e30ba757a2c25a9083d1b910"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.5.1/void-arm-pd-v3.5.1.tar.xz"
TARBALL_SHA256['arm']="77210c72f94ce879c99d9868152dc47581b1111cd3d36d9428d78f127eafa0ac"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v3.5.1/void-i686-pd-v3.5.1.tar.xz"
TARBALL_SHA256['i686']="3495b6e5b5d774a81f9ee1baaa0bdd87865544f57d63f8d8d950af41960988e3"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.5.1/void-x86_64-pd-v3.5.1.tar.xz"
TARBALL_SHA256['x86_64']="dc1bd6ce37f3668565310b23b0ab2181ae33ea680211b2541cce896b03c45644"

distro_setup() {
	# Set default shell to bash.
	run_proot_cmd usermod --shell /bin/bash root
}
