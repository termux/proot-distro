DISTRO_ROOTFS_URL_AARCH64="https://eu.mirror.archlinuxarm.org/os/ArchLinuxARM-aarch64-latest.tar.gz"
DISTRO_ROOTFS_URL_ARM="https://eu.mirror.archlinuxarm.org/os/ArchLinuxARM-armv7-latest.tar.gz"

# Will retrieve the correct file name from md5sums.txt as perhaps only it has
# persistent URL.
FILE_NAME=$(curl --fail --silent "https://mirror.rackspace.com/archlinux/iso/latest/md5sums.txt" | grep bootstrap | awk '{ print $2 }')
if [ -n "$FILE_NAME" ]; then
	DISTRO_ROOTFS_URL_X86_64="http://mirror.rackspace.com/archlinux/iso/latest/${FILE_NAME}"
fi
unset FILE_NAME

# x86_64 rootfs is stored in subdirectory.
if [ "$(uname -m)" = "x86_64" ]; then
	DISTRO_TARBALL_STRIP_OPT=1
fi

distro_setup() {
	# DNS resolver.
	rm -f ./etc/resolv.conf
	cat <<- EOF > ./etc/resolv.conf
	nameserver 1.1.1.1
	nameserver 1.0.0.1
	EOF
}
