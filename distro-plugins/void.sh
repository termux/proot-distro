# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Void Linux"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.18.0/void-aarch64-pd-v4.18.0.tar.xz"
TARBALL_SHA256['aarch64']="f19cb8c2f228f08e3dbebb47c800e46039e7d669e8a95507bbbad18c5d75f0f1"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.18.0/void-arm-pd-v4.18.0.tar.xz"
TARBALL_SHA256['arm']="6c38156bb2d32bbac62218adab9cda37b6689d467239397377525ffb2c053c29"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.18.0/void-i686-pd-v4.18.0.tar.xz"
TARBALL_SHA256['i686']="7c1124732e673a912c6495e924f91233c1cc0aa13010eef3b7b306c098e11145"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.18.0/void-x86_64-pd-v4.18.0.tar.xz"
TARBALL_SHA256['x86_64']="38183c84edf519f95ac728840b42b29505732b21a02570754d99aea743aa58a0"

distro_setup() {
	# Set default shell to bash.
	run_proot_cmd usermod --shell /bin/bash root
	# Fix issue where come CA certificates links may not be created.
	run_proot_cmd update-ca-certificates --fresh
}
