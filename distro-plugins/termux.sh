# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Termux"
DISTRO_TYPE="termux"
DISTRO_COMMENT="Official Termux environment sandbox."

TARBALL_URL['aarch64']="https://github.com/termux/termux-packages/releases/download/bootstrap-2025.12.21-r1%2Bapt.android-7/bootstrap-aarch64.zip"
TARBALL_SHA256['aarch64']="4ad1ef5c7b2d6a100be9e3e4d66f22ea652cf53d0de0e518a2a515b8e65dd684"
TARBALL_URL['arm']="https://github.com/termux/termux-packages/releases/download/bootstrap-2025.12.21-r1%2Bapt.android-7/bootstrap-arm.zip"
TARBALL_SHA256['arm']="430417911033cef2212089eb3f87264e5cb7eaa26087a96fbdacdd42d33e29a0"
TARBALL_URL['i686']="https://github.com/termux/termux-packages/releases/download/bootstrap-2025.12.21-r1%2Bapt.android-7/bootstrap-i686.zip"
TARBALL_SHA256['i686']="529d51ca07bc2620b3fa2c4931cf0f034dfa6fd4026b48b3054777810c66c08c"
TARBALL_URL['x86_64']="https://github.com/termux/termux-packages/releases/download/bootstrap-2025.12.21-r1%2Bapt.android-7/bootstrap-x86_64.zip"
TARBALL_SHA256['x86_64']="09f81d89422c28f6313e9b903454662b7ae4f72fb1429433da301a15400589af"

distro_setup() {
	# Create cache directory used by package manager.
	# It also will be created during login if was deleted.
	mkdir -p ./data/data/com.termux/cache

	# Run bootstrap second stage if exist.
	if [ -e ./data/data/com.termux/files/usr/etc/termux/termux-bootstrap/second-stage/termux-bootstrap-second-stage.sh ]; then
		run_proot_cmd bash ./data/data/com.termux/files/usr/etc/termux/termux-bootstrap/second-stage/termux-bootstrap-second-stage.sh
	fi
}
