dist_name="Rocky Linux"
dist_version="9.5"

bootstrap_distribution() {
	sudo rm -f "${ROOTFS_DIR}"/rocky-*.tar.xz

	for arch in aarch64 x86_64; do
		curl --fail --location --output "${WORKDIR}/Rocky-Container-Minimal.${arch}-${dist_version}.tar.xz" "https://download.rockylinux.org/pub/rocky/${dist_version%%.*}/images/${arch}/Rocky-${dist_version%%.*}-Container-Minimal.latest.${arch}.tar.xz"
		sudo rm -rf "${WORKDIR}/rocky-tmp" "${WORKDIR}/rocky-$(translate_arch "$arch")"
		mkdir "${WORKDIR}/rocky-tmp"
		tar -C "${WORKDIR}/rocky-tmp" -Jxf "${WORKDIR}/Rocky-Container-Minimal.${arch}-${dist_version}.tar.xz" --acls --xattrs --xattrs-include='*'

		sudo mkdir -m 755 "${WORKDIR}/rocky-$(translate_arch "$arch")"
		sudo mv "${WORKDIR}/rocky-tmp"/* "${WORKDIR}/rocky-$(translate_arch "$arch")"
		sudo rm -rf "${WORKDIR}/rocky-tmp"
		
		cat <<- EOF | sudo unshare -mpf bash -e -
		rm -f "${WORKDIR}/rocky-$(translate_arch "$arch")/etc/resolv.conf"
		echo "nameserver 1.1.1.1" > "${WORKDIR}/rocky-$(translate_arch "$arch")/etc/resolv.conf"
		mount --bind /dev "${WORKDIR}/rocky-$(translate_arch "$arch")/dev"
		mount --bind /proc "${WORKDIR}/rocky-$(translate_arch "$arch")/proc"
		mount --bind /sys "${WORKDIR}/rocky-$(translate_arch "$arch")/sys"
		chroot "${WORKDIR}/rocky-$(translate_arch "$arch")" microdnf upgrade -y
		chroot "${WORKDIR}/rocky-$(translate_arch "$arch")" microdnf clean all -y
		EOF

		archive_rootfs "${ROOTFS_DIR}/rocky-$(translate_arch "$arch")-pd-${CURRENT_VERSION}.tar.xz" "rocky-$(translate_arch "$arch")"
	done
	unset arch
}

write_plugin() {
	cat <<- EOF > "${PLUGIN_DIR}/rockylinux.sh"
	# This is a default distribution plug-in.
	# Do not modify this file as your changes will be overwritten on next update.
	# If you want customize installation, please make a copy.
	DISTRO_NAME="Rocky Linux"
	DISTRO_COMMENT="Version ${dist_version}. "

	TARBALL_URL['aarch64']="${GIT_RELEASE_URL}/rocky-aarch64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['aarch64']="$(sha256sum "${ROOTFS_DIR}/rocky-aarch64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"
	TARBALL_URL['x86_64']="${GIT_RELEASE_URL}/rocky-x86_64-pd-${CURRENT_VERSION}.tar.xz"
	TARBALL_SHA256['x86_64']="$(sha256sum "${ROOTFS_DIR}/rocky-x86_64-pd-${CURRENT_VERSION}.tar.xz" | awk '{ print $1}')"

	EOF
}
