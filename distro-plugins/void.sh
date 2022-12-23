# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Void Linux"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.3.0/void-aarch64-pd-v3.3.0.tar.xz"
TARBALL_SHA256['aarch64']="bd52f824edc088ba12e6bce7d4e4a5f8c35c27d6ce903d10fb1d42c678cd13dd"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.3.0/void-arm-pd-v3.3.0.tar.xz"
TARBALL_SHA256['arm']="6e72bde7ecea6bb0805cef6647ca7dfc41b96c39599cb5fc6f8cf8a97d6c24eb"
TARBALL_URL['i686']="https://github.com/termux/proot-distro/releases/download/v3.3.0/void-i686-pd-v3.3.0.tar.xz"
TARBALL_SHA256['i686']="ee4ac17b78afd93bec75f5ae9c7d8112966c788319e5b035fa2009c949825326"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.3.0/void-x86_64-pd-v3.3.0.tar.xz"
TARBALL_SHA256['x86_64']="6e4f46c129e499d493bc6cfe6334a5814b15593b75b6abfbdd05c5be3565c4dc"

distro_setup() {
	# Set default shell to bash.
	run_proot_cmd usermod --shell /bin/bash root
}
