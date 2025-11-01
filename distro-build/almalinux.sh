dist_name="AlmaLinux"
dist_version="10"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/almalinux-*.tar.xz

	for arch in arm64 amd64; do
		curl --fail --location --output "almalinux-${dist_version}-minimal-${arch}.tar.xz" "https://github.com/AlmaLinux/container-images/raw/refs/heads/${dist_version}/minimal/${arch}/almalinux-${dist_version}-minimal-${arch}.tar.xz"
		sudo rm -rf "${WORKDIR}/almalinux-tmp" "${WORKDIR}/almalinux-$(translate_arch "$arch")"
		mkdir "${WORKDIR}/almalinux-tmp"
		tar -C "${WORKDIR}/almalinux-tmp" -Jxf "${WORKDIR}/almalinux-${dist_version}-minimal-${arch}.tar.xz"
		oci_manifest=$(jq -r '.manifests[0].digest' "${WORKDIR}/almalinux-tmp"/index.json | cut -d ':' -f 2)
		oci_layers=$(jq -r '.layers[].digest' "${WORKDIR}/almalinux-tmp/blobs/sha256/${oci_manifest}" | cut -d ':' -f 2)

		sudo mkdir -m 755 "${WORKDIR}/almalinux-$(translate_arch "$arch")"
		for layer in ${oci_layers}; do
			sudo tar -zxp --acls --xattrs --xattrs-include='*' \
				-f "${WORKDIR}/almalinux-tmp/blobs/sha256/${layer}" \
				-C "${WORKDIR}/almalinux-$(translate_arch "$arch")"
		done
		sudo rm -rf "${WORKDIR}/almalinux-tmp"

		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/almalinux-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/almalinux-$(translate_arch "$arch")/etc/resolv.conf"
		mount --bind /dev "${WORKDIR}/almalinux-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/almalinux-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/almalinux-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/almalinux-$(translate_arch "$arch")" microdnf upgrade -y
		chroot "${WORKDIR}/almalinux-$(translate_arch "$arch")" microdnf install dnf -y
		chroot "${WORKDIR}/almalinux-$(translate_arch "$arch")" microdnf clean all -y
		EOF

		archive_rootfs "${ROOTFS_DIR}/almalinux-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" "almalinux-$(translate_arch "$arch")"
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
