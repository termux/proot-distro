dist_name="AlmaLinux"
dist_version="10"

bootstrap_distribution() {
    sudo rm -f "${ROOTFS_DIR}"/almalinux-*.tar.xz

	for arch in arm64 amd64; do
		curl --fail --location \
			--output "${WORKDIR}/almalinux-$(translate_arch "$arch").tar.xz" \
			"https://github.com/AlmaLinux/container-images/raw/refs/heads/${dist_version}/default/${arch}/almalinux-${dist_version}-default-${arch}.tar.xz"

		sudo rm -rf "${WORKDIR}/almalinux-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/almalinux-$(translate_arch "$arch")"
		sudo tar -Jxp --acls --xattrs --xattrs-include='*' \
			-f "${WORKDIR}/almalinux-$(translate_arch "$arch").tar.xz" \
			-C "${WORKDIR}/almalinux-$(translate_arch "$arch")"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/almalinux-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/almalinux-$(translate_arch "$arch")/etc/resolv.conf"
		echo "excludepkgs=*selinux* filesystem" >> "${WORKDIR}/almalinux-$(translate_arch "$arch")/etc/dnf/dnf.conf"
		mount --bind /dev "${WORKDIR}/almalinux-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/almalinux-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/almalinux-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/almalinux-$(translate_arch "$arch")" dnf upgrade -y
		chroot "${WORKDIR}/almalinux-$(translate_arch "$arch")" dnf install -y passwd util-linux
		chroot "${WORKDIR}/almalinux-$(translate_arch "$arch")" dnf clean all
		chmod 4755 "${WORKDIR}/almalinux-$(translate_arch "$arch")"/usr/bin/sudo
		EOF

		archive_rootfs "${ROOTFS_DIR}/almalinux-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"almalinux-$(translate_arch "$arch")"
	done
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/almalinux.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="AlmaLinux"
	DISTRO_COMMENT="Version ${dist_version}."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/almalinux-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/almalinux-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
