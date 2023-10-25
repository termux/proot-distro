# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Void Linux"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.18.1/void-aarch64-pd-v3.18.1.tar.xz"
TARBALL_SHA256['aarch64']="a2f17acfc49e6f1a6f2d121cb6f69e5d64ff3bc642364939acd5eba5738c4467"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.18.1/void-arm-pd-v3.18.1.tar.xz"
TARBALL_SHA256['arm']="aa2d01e8e383ea65374ae70f6ad47e63ea93ab44c4c6580cb138f7fae354dbae"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v3.18.1/void-i686-pd-v3.18.1.tar.xz"
TARBALL_SHA256['i686']="5dd0d58ee4abde72e68137c10d5b0a6866a9ec2d88d9f834bc56864cfc96c662"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.18.1/void-x86_64-pd-v3.18.1.tar.xz"
TARBALL_SHA256['x86_64']="c08229efd5a7dfca6f496e774d52a9c8cbbbb3f19189c2482343872159ec6325"

distro_setup() {
	# Set default shell to bash.
	run_proot_cmd usermod --shell /bin/bash root
	# Fix issue where come CA certificates links may not be created.
	run_proot_cmd update-ca-certificates --fresh
}
