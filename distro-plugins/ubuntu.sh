# Doesn't support x86 32 bit.
DISTRO_ROOTFS_URL_AARCH64="https://partner-images.canonical.com/core/focal/current/ubuntu-focal-core-cloudimg-arm64-root.tar.gz"
DISTRO_ROOTFS_URL_ARM="https://partner-images.canonical.com/core/focal/current/ubuntu-focal-core-cloudimg-armhf-root.tar.gz"
DISTRO_ROOTFS_URL_X86_64="https://partner-images.canonical.com/core/focal/current/ubuntu-focal-core-cloudimg-amd64-root.tar.gz"

distro_setup() {
	# DNS resolver.
	rm -f ./etc/resolv.conf
	cat <<- EOF > ./etc/resolv.conf
	nameserver 1.1.1.1
	nameserver 1.0.0.1
	EOF
}
