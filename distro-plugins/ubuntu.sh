# This is a default distribution plug-in.
# Do not modify this file as your changes will be overwritten on next update.
# If you want customize installation, please make a copy.
DISTRO_NAME="Ubuntu (20.04)"

TARBALL_URL['aarch64']="https://github.com/termux/proot-distro/releases/download/v1.10.1/ubuntu-aarch64-pd-v1.10.1.tar.xz"
TARBALL_SHA256['aarch64']="145394dda8aaa1ec09d22b23479e9c95eba986515afb8aec68be5d9b79e8db87"
TARBALL_URL['arm']="https://github.com/termux/proot-distro/releases/download/v1.10.1/ubuntu-arm-pd-v1.10.1.tar.xz"
TARBALL_SHA256['arm']="0673b01a6d592b9058525a038093617cb91e1c257baf95be82287dd3f1e6f747"
TARBALL_URL['x86_64']="https://github.com/termux/proot-distro/releases/download/v1.10.1/ubuntu-x86_64-pd-v1.10.1.tar.xz"
TARBALL_SHA256['x86_64']="597ec5859bde0577a31c5958aae9fd1eee897d02753af4a6e15816ebfd03301a"

distro_setup() {
	# Enable additional repository components.
	if [ "$DISTRO_ARCH" = "amd64" ]; then
		echo "deb http://archive.ubuntu.com/ubuntu focal main universe multiverse" > ./etc/apt/sources.list
	else
		echo "deb http://ports.ubuntu.com/ubuntu-ports focal main universe multiverse" > ./etc/apt/sources.list
	fi

	# Don't update gvfs-daemons and udisks2
	run_proot_cmd apt-mark hold gvfs-daemons udisks2
}
