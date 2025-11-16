dist_name="Oracle Linux"
dist_version="10"

bootstrap_distribution() {
    sudo rm -f "${ROOTFS_DIR}"/oraclelinux-*.tar.xz

	for arch in arm64v8 amd64; do
		curl --fail --location \
			--output "${WORKDIR}/oraclelinux-$(translate_arch "$arch").tar.xz" \
			"https://github.com/oracle/container-images/raw/dist-${arch}/${dist_version}/oraclelinux-${dist_version}-${arch}-rootfs.tar.xz"

		sudo rm -rf "${WORKDIR}/oraclelinux-$(translate_arch "$arch")"
		sudo mkdir -m 755 "${WORKDIR}/oraclelinux-$(translate_arch "$arch")"
		sudo tar -Jxp --acls --xattrs --xattrs-include='*' \
			-f "${WORKDIR}/oraclelinux-$(translate_arch "$arch").tar.xz" \
			-C "${WORKDIR}/oraclelinux-$(translate_arch "$arch")"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/oraclelinux-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/oraclelinux-$(translate_arch "$arch")/etc/resolv.conf"
		echo "excludepkgs=*selinux* filesystem" >> "${WORKDIR}/oraclelinux-$(translate_arch "$arch")/etc/dnf/dnf.conf"
		mount --bind /dev "${WORKDIR}/oraclelinux-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/oraclelinux-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/oraclelinux-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/oraclelinux-$(translate_arch "$arch")" dnf upgrade -y
		chroot "${WORKDIR}/oraclelinux-$(translate_arch "$arch")" dnf install -y passwd util-linux
		chroot "${WORKDIR}/oraclelinux-$(translate_arch "$arch")" dnf clean all
		chmod 4755 "${WORKDIR}/oraclelinux-$(translate_arch "$arch")"/usr/bin/sudo
		EOF

		archive_rootfs "${ROOTFS_DIR}/oraclelinux-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" \
			"oraclelinux-$(translate_arch "$arch")"
	done
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/oracle.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Oracle Linux"
	DISTRO_COMMENT="Version ${dist_version}."

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/oraclelinux-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/oraclelinux-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/oraclelinux-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/oraclelinux-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	EOF
}
