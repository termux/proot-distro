# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Void Linux"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v4.22.1/void-aarch64-pd-v4.22.1.tar.xz"
TARBALL_SHA256['aarch64']="4430ed51e4c68252ee968c6ea19b1e00333ee9e77f1bda690901632b76322139"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v4.22.1/void-arm-pd-v4.22.1.tar.xz"
TARBALL_SHA256['arm']="03c3b23d44c5c1f913c00e28204c4566a83dbf451db4d91cfb14f366950f99c8"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v4.22.1/void-i686-pd-v4.22.1.tar.xz"
TARBALL_SHA256['i686']="69903dd6ea907a17a3be43ef8163d8146227c557919058b9b014102a857f8dfa"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v4.22.1/void-x86_64-pd-v4.22.1.tar.xz"
TARBALL_SHA256['x86_64']="c84e1927c584c7fa1f12662e572f6d1e2c653a4cb712faa1aaddc8e37ed46708"

distro_setup() {
	# Set default shell to bash.
	run_proot_cmd usermod --shell /bin/bash root
	# Fix issue where come CA certificates links may not be created.
	run_proot_cmd update-ca-certificates --fresh
}
