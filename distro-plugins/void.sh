# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Void Linux"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.11.0/void-aarch64-pd-v3.11.0.tar.xz"
TARBALL_SHA256['aarch64']="879b4ddced8cb8723193c88b53cbafdd5bf8bd6f0f1fb6445c3a8a184329f105"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.11.0/void-arm-pd-v3.11.0.tar.xz"
TARBALL_SHA256['arm']="b372b38eef61da991efa423fa31c644e8a156d7f15c438a492c37d68859a21a5"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v3.11.0/void-i686-pd-v3.11.0.tar.xz"
TARBALL_SHA256['i686']="5d90191fce3968cd4993f3c6a361c5e20ec21aa149adc113ada527a8132a3a2c"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.11.0/void-x86_64-pd-v3.11.0.tar.xz"
TARBALL_SHA256['x86_64']="676546678dc2fdfc4e5ffa5c3dc8b3aa5dc8b884108fdf2346f4edd0311679bc"

distro_setup() {
	# Set default shell to bash.
	run_proot_cmd usermod --shell /bin/bash root
	# Fix issue where come CA certificates links may not be created.
	run_proot_cmd update-ca-certificates --fresh
}
