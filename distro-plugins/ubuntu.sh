# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (jammy)"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v3.0.1/ubuntu-aarch64-pd-v3.0.1.tar.xz"
TARBALL_SHA256['aarch64']="a5403d61f40a72a5c1fd0d9ef5368cf08a23b0f40442e1e60fe4601de5c0c2ab"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v3.0.1/ubuntu-arm-pd-v3.0.1.tar.xz"
TARBALL_SHA256['arm']="04b736a3cf2ff57027d396013aab778cfe2a6800108a779af480a264f634d8f3"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v3.0.1/ubuntu-x86_64-pd-v3.0.1.tar.xz"
TARBALL_SHA256['x86_64']="9ad3e8f4de4a85d8dae3a88c1140da0058d5bbae65a0717bb06f1ccecad0d7a6"

distro_setup() {
	# Don't update udisks2
	run_proot_cmd apt-mark hold udisks2
}
