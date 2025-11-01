dist_name="AlmaLinux"
dist_version="10"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/almalinux-*.tar.xz

	for arch in arm64 amd64; do
		curl --fail --location --output "almalinux-${dist_version}-minimal-${arch}.tar.xz" "https://github.com/AlmaLinux/container-images/raw/refs/heads/${dist_version}/minimal/${arch}/almalinux-${dist_version}-minimal-${arch}.tar.xz"
		sudo rm -rf "${WORKDIR}/almalinux-tmp" "${WORKDIR}/almalinux-${arch}
		mkdir "${WORKDIR}/almalinux-tmp"
		tar -C "${WORKDIR}/almalinux-tmp" -Jxf "${WORKDIR}/almalinux-${dist_version}-minimal-${arch}.tar.xz"
		sudo mkdir -m 755 "${WORKDIR}/almalinux-${arch}"

		cat <<- EOF | sudo unshare -mpf bash -e -
		echo "nameserver 1.1.1.1" > "${WORKDIR}/almalinux-${arch}")/etc/resolv.conf"
		mount --bind /dev "${WORKDIR}/almalinux-${arch}/dev"
		mount --bind /proc "${WORKDIR}/almalinux-${arch}/proc"
		mount --bind /sys "${WORKDIR}/almalinux-${arch}/sys"
		chroot "${WORKDIR}/almalinux-${arch}" microdnf upgrade -y
		chroot "${WORKDIR}/almalinux-${arch}" microdnf install dnf -y
		chroot "${WORKDIR}/almalinux-${arch}" microdnf clean all -y
		EOF

		archive_rootfs "${ROOTFS_DIR}/almalinux-${arch}-pd-${CURRENT_VERSION}.tar.xz" "almalinux-${arch}"
		mv almalinux-arm64-pd-${CURRENT_VERSION}.tar.xz almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz 
        mv almalinux-amd64-pd-${CURRENT_VERSION}.tar.xz almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz 
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/almalinux.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="AlmaLinux"
	DISTRO_COMMENT="Version ${dist_version}"

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
